"""Biblioteca pessoal de videos e controle do reprodutor Samsung DLNA."""
import argparse
from contextlib import contextmanager
import hashlib
import html
import http.server
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import tmdb_covers

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SALA_TV_DATA_DIR', str(ROOT / 'dados'))).expanduser().resolve()
DATA.mkdir(parents=True,exist_ok=True)
(DATA / 'capas').mkdir(exist_ok=True)
DB = DATA / 'biblioteca.sqlite'
PORT = 8768
TOKEN = secrets.token_hex(24)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.ts', '.webm'}
LOCK = threading.RLock()
CONTROL = threading.RLock()
ENDING = threading.Event()
STATE = {'active': None, 'pending': False, 'state': 'IDLE', 'message': 'Escolha um título para assistir.', 'connected': False}
COVER_JOB={'running':False,'done':0,'total':0,'message':''}
ASSET_LOCK=threading.RLock()
MISSING_FILES={}

@contextmanager
def connect():
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()

def initialize():
    with connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS media (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'filme', duration REAL NOT NULL DEFAULT 0,
            position REAL NOT NULL DEFAULT 0, subtitle TEXT, last_watched REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        db.execute("INSERT OR IGNORE INTO settings VALUES ('tv_ip',?)", (os.environ.get('SALA_TV_TV_IP','192.168.1.100'),))
        db.execute("INSERT OR IGNORE INTO settings VALUES ('tmdb_access','')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('initial_import_done','1')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('control_path','/upnp/control/AVTransport1')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('control_port','9197')")
        columns={row['name'] for row in db.execute('PRAGMA table_info(media)')}
        for name,definition in (('series_name',"TEXT NOT NULL DEFAULT ''"),('season','INTEGER NOT NULL DEFAULT 1'),('episode','INTEGER NOT NULL DEFAULT 0'),('tmdb_id','INTEGER'),('tmdb_type',"TEXT NOT NULL DEFAULT ''"),('poster_revision','INTEGER NOT NULL DEFAULT 0')):
            if name not in columns:
                db.execute(f'ALTER TABLE media ADD COLUMN {name} {definition}')
        for row in db.execute("SELECT id,path,title FROM media WHERE kind='serie' AND series_name='' ").fetchall():
            name,season,episode=episode_info(Path(row['path']))
            db.execute('UPDATE media SET series_name=?,season=?,episode=? WHERE id=?',(name or row['title'],season,episode,row['id']))

def episode_info(path):
    match=re.search(r'(?i)S(\d{1,2})[ ._-]*E(\d{1,3})|\b(\d{1,2})x(\d{1,3})\b',path.stem)
    if not match:
        return '',1,0
    prefix=path.stem[:match.start()].replace('.',' ').replace('_',' ').strip(' -')
    if not prefix:
        parent=path.parent
        if re.fullmatch(r'(?i)(?:season|temporada|s)[ ._-]*\d+',parent.name):
            parent=parent.parent
        prefix=parent.name.replace('.',' ').replace('_',' ').strip()
    return prefix,int(match[1] or match[3]),int(match[2] or match[4])

def setting(key):
    with connect() as db:
        return db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]

def tmdb_access():
    value=setting('tmdb_access')
    return tmdb_covers.protect(value,decrypt=True) if value else ''

def choose_cover(media_id,kind,ident):
    item=get_media(media_id)
    expected='tv' if item['kind']=='serie' else 'movie'
    if kind!=expected:
        raise ValueError('Escolha uma capa correspondente à categoria do título.')
    targets=[item]
    if item['kind']=='serie':
        with connect() as db:
            rows=[dict(row) for row in db.execute("SELECT * FROM media WHERE kind='serie'")]
        name=(item['series_name'] or item['title']).casefold()
        targets=[row for row in rows if (row['series_name'] or row['title']).casefold()==name]
    first=DATA/'capas'/f'{media_id}.tmdb.jpg'
    tmdb_covers.fetch_poster(tmdb_access(),kind,ident,first)
    image=first.read_bytes()
    with ASSET_LOCK:
        with connect() as db:
            if not db.execute('SELECT 1 FROM media WHERE id=?',(media_id,)).fetchone():
                delete_cover_file(first)
                raise ValueError('Este título já foi removido da biblioteca.')
            for row in targets:
                if not db.execute('SELECT 1 FROM media WHERE id=?',(row['id'],)).fetchone():
                    continue
                destination=DATA/'capas'/f'{row["id"]}.tmdb.jpg'
                if destination!=first:
                    temporary=destination.with_suffix('.tmp')
                    temporary.write_bytes(image)
                    temporary.replace(destination)
                db.execute('UPDATE media SET tmdb_id=?,tmdb_type=?,poster_revision=poster_revision+1 WHERE id=?',(int(ident),kind,row['id']))

def find_auto_cover(ident):
    item=get_media(ident)
    if item['tmdb_id']:
        return 'A capa já foi escolhida.'
    if item['kind']=='serie':
        with connect() as db:
            rows=[dict(row) for row in db.execute("SELECT * FROM media WHERE kind='serie' AND tmdb_id IS NOT NULL")]
        match=next((r for r in rows if (r['series_name'] or r['title']).casefold()==(item['series_name'] or item['title']).casefold()),None)
        if match:
            choose_cover(ident,'tv',match['tmdb_id'])
            return 'Capa da série associada.'
    candidates=tmdb_covers.search(tmdb_access(),'tv' if item['kind']=='serie' else 'movie',item['series_name'] or item['title'])
    selected=tmdb_covers.automatic_choice(candidates)
    if selected:
        choose_cover(ident,selected['kind'],selected['id'])
        return 'Capa encontrada.'
    return 'Há títulos semelhantes. Escolha a capa em Detalhes.' if candidates else 'Nenhuma capa encontrada para este nome.'

def update_covers():
    with LOCK:
        if COVER_JOB['running']:
            raise ValueError('A busca de capas já está em andamento.')
        if not setting('tmdb_access'):
            raise ValueError('Configure uma chave ou token em Capas e API.')
        COVER_JOB.update(running=True,done=0,total=0,message='Buscando capas…')
    def work():
        try:
            with connect() as db:
                items=[dict(row) for row in db.execute('SELECT * FROM media WHERE tmdb_id IS NULL')]
            seen=set()
            selected=[]
            for item in items:
                key=('serie',(item['series_name'] or item['title']).casefold()) if item['kind']=='serie' else ('filme',item['id'])
                if key not in seen:
                    seen.add(key)
                    selected.append(item)
            with LOCK: COVER_JOB['total']=len(selected)
            for item in selected:
                result=find_auto_cover(item['id'])
                with LOCK:
                    COVER_JOB['done']+=1
                    COVER_JOB['message']=result
            with LOCK: COVER_JOB['message']='Busca concluída. Confira as capas em Detalhes se houver nomes semelhantes.'
        except Exception as exc:
            with LOCK: COVER_JOB['message']=str(exc)
        finally:
            with LOCK: COVER_JOB['running']=False
    threading.Thread(target=work,daemon=True).start()

def get_media(ident):
    with connect() as db:
        row = db.execute('SELECT * FROM media WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('Título não encontrado na biblioteca.')
    return dict(row)

def seconds(value):
    try:
        a, b, c = value.split(':')
        return int(a)*3600 + int(b)*60 + float(c)
    except (ValueError, AttributeError):
        return None

def clock(value):
    value = int(value)
    return f'{value//3600:02}:{value//60%60:02}:{value%60:02}'

def soap(action, args):
    tv = setting('tv_ip')
    namespace = 'urn:schemas-upnp-org:service:AVTransport:1'
    envelope = ET.Element('{http://schemas.xmlsoap.org/soap/envelope/}Envelope')
    body = ET.SubElement(envelope, '{http://schemas.xmlsoap.org/soap/envelope/}Body')
    command = ET.SubElement(body, f'{{{namespace}}}{action}')
    for name, value in args.items():
        ET.SubElement(command, name).text = str(value)
    request = urllib.request.Request(f'http://{tv}:{setting("control_port")}{setting("control_path")}',
        data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
        headers={'Content-Type':'text/xml; charset="utf-8"', 'SOAPAction':f'"{namespace}#{action}"'})
    try:
        with OPENER.open(request, timeout=5) as response:
            doc = ET.fromstring(response.read(100000))
    except urllib.error.HTTPError as exc:
        try:
            fault = ET.fromstring(exc.read())
            detail = next((x.text for x in fault.iter() if x.tag.endswith('errorDescription')), str(exc))
        except Exception:
            detail = str(exc)
        raise RuntimeError(f'A TV respondeu: {detail}') from exc
    return {x.tag.split('}')[-1]: x.text for x in doc.iter() if not len(x)}

def pc_ip():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
        route.connect((setting('tv_ip'),9197))
        return route.getsockname()[0]

def media_url(ident):
    suffix = Path(get_media(ident)['path']).suffix.lower()
    return f'http://{pc_ip()}:{PORT}/media/{ident}/movie{suffix}'

def media_mime(path):
    return {'.mkv':'video/x-mkv','.avi':'video/x-msvideo','.ts':'video/mpeg'}.get(Path(path).suffix.lower(),mimetypes.guess_type(str(path))[0] or 'video/mp4')

def save_position(ident, pos, duration=0):
    # Um STOP pode devolver zero; ele nao deve apagar o ultimo ponto valido.
    if pos is None or pos <= 0:
        return
    with connect() as db:
        row = db.execute('SELECT duration FROM media WHERE id=?',(ident,)).fetchone()
        if not row:
            return
        duration = duration or row['duration']
        if duration and pos > duration + 5:
            return
        pos = 0 if duration and pos >= duration-10 else pos
        db.execute('UPDATE media SET position=?,duration=CASE WHEN ?>0 THEN ? ELSE duration END,last_watched=? WHERE id=?',
            (pos,duration,duration,time.time(),ident))

def observe():
    with LOCK:
        active, pending = STATE['active'], STATE['pending']
    if pending:
        return
    try:
        position = soap('GetPositionInfo',{'InstanceID':0})
        transport = soap('GetTransportInfo',{'InstanceID':0})
        uri = position.get('TrackURI','') or ''
        match = re.search(r'/media/([a-f0-9]{20})/movie\.', uri)
        if match:
            ident = match.group(1)
            try:
                get_media(ident)
            except ValueError:
                ident = None
            if ident:
                active = ident
                current=seconds(position.get('RelTime'))
                with LOCK: preserve=STATE.get('preserve_bookmark')==ident
                if preserve and current is not None and current>=get_media(ident)['position']-10:
                    with LOCK: STATE.pop('preserve_bookmark',None)
                    preserve=False
                if not preserve:
                    save_position(ident,current,seconds(position.get('TrackDuration')) or 0)
        elif active:
            # Outro aplicativo/filme foi selecionado na TV; nao usar seu tempo aqui.
            active = None
        with LOCK:
            if transport.get('CurrentTransportState') in ('STOPPED','NO_MEDIA_PRESENT'):
                active=None
            STATE.update(active=active,state=transport.get('CurrentTransportState','UNKNOWN'),connected=True)
    except Exception:
        with LOCK:
            STATE['connected'] = False

def monitor():
    while not ENDING.wait(2):
        if CONTROL.acquire(blocking=False):
            try:
                observe()
            finally:
                CONTROL.release()

def video_file_state(path):
    path=Path(path)
    try:
        return 'present' if stat.S_ISREG(path.stat().st_mode) else 'missing'
    except FileNotFoundError:
        # Uma pasta apagada pode ter sumido; uma unidade fora do ar nao e exclusao.
        try:
            root=Path(path.anchor)
            if not path.anchor or not stat.S_ISDIR(root.stat().st_mode):
                return 'unavailable'
        except OSError:
            return 'unavailable'
        return 'missing'
    except OSError:
        return 'unavailable'

def delete_cover_file(path):
    directory=(DATA/'capas').resolve()
    if path.parent.resolve()!=directory or not re.fullmatch(r'[a-f0-9]{20}(?:\.tmdb)?\.(?:jpg|tmp)',path.name):
        return
    try:
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            return
        if not path.resolve().is_relative_to(directory):
            return
        path.unlink()
    except (OSError,ValueError):
        pass

def remove_media(ident,automatic=False):
    with CONTROL:
        with LOCK:
            active=STATE['active']==ident
        if active and not automatic:
            raise ValueError('Pare a reprodução antes de remover este título.')
        if active:
            try: soap('Stop',{'InstanceID':0})
            except Exception: pass
            with LOCK: STATE.update(active=None,message='O arquivo foi apagado e o título foi removido da biblioteca.')
        with ASSET_LOCK:
            with connect() as db:
                db.execute('DELETE FROM media WHERE id=?',(ident,))
            for suffix in ('.jpg','.tmdb.jpg','.tmdb.tmp'):
                delete_cover_file(DATA/'capas'/f'{ident}{suffix}')
        MISSING_FILES.pop(ident,None)

def clean_unused_covers(now=None):
    now=time.time() if now is None else now
    with ASSET_LOCK:
        with connect() as db:
            referenced={row['id'] for row in db.execute('SELECT id FROM media')}
        for asset in (DATA/'capas').iterdir():
            match=re.fullmatch(r'([a-f0-9]{20})(?:\.tmdb)?\.(?:jpg|tmp)',asset.name)
            if match and match[1] not in referenced:
                try:
                    if now-asset.lstat().st_mtime>=30:
                        delete_cover_file(asset)
                except OSError:
                    pass

def sync_library(now=None,grace=10):
    now=time.monotonic() if now is None else now
    with connect() as db:
        items=[dict(row) for row in db.execute('SELECT id,path FROM media')]
    existing_ids={item['id'] for item in items}
    for ident in list(MISSING_FILES):
        if ident not in existing_ids:
            MISSING_FILES.pop(ident,None)
    for item in items:
        status=video_file_state(item['path'])
        ident=item['id']
        if status!='missing':
            MISSING_FILES.pop(ident,None)
            continue
        since=MISSING_FILES.setdefault(ident,now)
        if now-since>=grace:
            # Confirma novamente para tolerar renomeacoes ou copias em andamento.
            if video_file_state(item['path'])=='missing':
                remove_media(ident,automatic=True)
    clean_unused_covers()

def maintenance():
    while not ENDING.is_set():
        try: sync_library()
        except Exception as exc: print('Sincronizacao da biblioteca:',exc,flush=True)
        if ENDING.wait(10):
            break

def ffmpeg():
    explicit=os.environ.get('SALA_TV_FFMPEG')
    if explicit:
        path=Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    try:
        import imageio_ffmpeg
        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError,RuntimeError):
        return None

def add_file(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
        raise ValueError('Selecione um arquivo de vídeo existente.')
    ident = hashlib.sha256(str(path).casefold().encode()).hexdigest()[:20]
    with connect() as db:
        if db.execute('SELECT 1 FROM media WHERE id=?',(ident,)).fetchone():
            return ident
    stem = path.stem
    title = re.split(r'(?i)\b(?:1080p|720p|2160p|bluray|webrip|web-dl|hdtv)\b',stem)[0].replace('.',' ').replace('_',' ').strip(' -')
    series_name,season,episode=episode_info(path)
    kind = 'serie' if series_name else 'filme'
    subtitles = list(path.parent.glob('*.srt'))
    subtitle = path.with_suffix('.srt')
    if not subtitle.is_file():
        subtitle = next((x for x in subtitles if x.stem.casefold().startswith(stem.casefold())), None)
        if subtitle is None and len(subtitles)==1:
            subtitle = subtitles[0]
    duration = 0
    encoder = ffmpeg()
    if encoder:
        try:
            result = subprocess.run([str(encoder),'-hide_banner','-i',str(path),'-t','0','-f','null','-'],
                capture_output=True,text=True,errors='replace',timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
            found = re.search(r'Duration: (\d+:\d+:\d+\.\d+)',result.stderr)
            duration = seconds(found.group(1)) if found else 0
        except (OSError,subprocess.TimeoutExpired):
            pass
    with connect() as db:
        db.execute('INSERT INTO media(id,path,title,kind,duration,subtitle,series_name,season,episode) VALUES (?,?,?,?,?,?,?,?,?)',
            (ident,str(path),title,kind,duration,str(subtitle) if subtitle else None,series_name,season,episode))
    if encoder:
        def cover():
            try:
                subprocess.run([str(encoder),'-hide_banner','-loglevel','error','-ss',str(min(60,max(0,duration/4))),
                    '-i',str(path),'-frames:v','1','-vf','scale=480:-1','-q:v','3','-y',str(DATA/'capas'/f'{ident}.jpg')],
                    capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError,subprocess.TimeoutExpired):
                pass
            finally:
                with ASSET_LOCK:
                    try: get_media(ident)
                    except ValueError: delete_cover_file(DATA/'capas'/f'{ident}.jpg')
        threading.Thread(target=cover,daemon=True).start()
    if setting('tmdb_access'):
        def auto_cover():
            try: find_auto_cover(ident)
            except Exception: pass
        threading.Thread(target=auto_cover,daemon=True).start()
    return ident

def play(ident, resume=True):
    with CONTROL:
        observe()
        item = get_media(ident)
        if not Path(item['path']).is_file():
            raise ValueError('O arquivo foi movido ou não está disponível.')
        target = max(0,item['position']-3) if resume else 0
        url = media_url(ident)
        caption = f'http://{pc_ip()}:{PORT}/media/{ident}/movie.srt'
        subtitle = item['subtitle'] and Path(item['subtitle']).is_file()
        metadata = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
            'xmlns:sec="http://www.sec.co.kr/"><item id="'+ident+'" parentID="0" restricted="1">'
            '<dc:title>'+html.escape(item['title'])+'</dc:title><upnp:class>object.item.videoItem</upnp:class>'
            f'<res size="{Path(item["path"]).stat().st_size}" duration="{clock(item["duration"])}" '
            f'protocolInfo="http-get:*:{media_mime(item["path"])}:DLNA.ORG_OP=01;DLNA.ORG_CI=0">{url}</res>'
            +(f'<sec:CaptionInfoEx sec:type="srt">{caption}</sec:CaptionInfoEx>' if subtitle else '')+'</item></DIDL-Lite>')
        with LOCK:
            STATE.update(pending=True,message='Enviando à TV…')
        try:
            soap('SetAVTransportURI',{'InstanceID':0,'CurrentURI':url,'CurrentURIMetaData':metadata})
            try:
                soap('Play',{'InstanceID':0,'Speed':1})
            except RuntimeError:
                pass  # O firmware pode iniciar sozinho ao receber a URI.
            with LOCK:
                STATE['active'] = ident
            if target >= 5:
                success = False
                for _ in range(15):
                    time.sleep(1)
                    try:
                        transport = soap('GetTransportInfo',{'InstanceID':0})
                        if transport.get('CurrentTransportState') not in ('PLAYING','PAUSED_PLAYBACK'):
                            continue
                        soap('Seek',{'InstanceID':0,'Unit':'REL_TIME','Target':clock(target)})
                        time.sleep(.5)
                        pos = seconds(soap('GetPositionInfo',{'InstanceID':0}).get('RelTime'))
                        if pos is not None and abs(pos-target)<10:
                            success = True
                            break
                    except Exception:
                        continue
                message = f'Retomando em {clock(target)}.' if success else 'A TV não confirmou a retomada. Seu ponto salvo foi preservado.'
                if not success:
                    with LOCK:
                        STATE['preserve_bookmark'] = ident
            else:
                if not resume:
                    with connect() as db:
                        db.execute('UPDATE media SET position=0 WHERE id=?',(ident,))
                message = 'Enviado à TV. Use o controle remoto para pausar e continuar.'
            with LOCK:
                STATE['message'] = message
        finally:
            with LOCK:
                STATE['pending'] = False

def native_pick(mode):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        if mode=='folder':
            result = filedialog.askdirectory(title='Adicionar pasta de filmes ou episódios',parent=root)
        elif mode=='subtitle':
            result = filedialog.askopenfilename(title='Selecionar legenda SRT',filetypes=[('Legenda SRT','*.srt')],parent=root)
        else:
            result = list(filedialog.askopenfilenames(title='Adicionar filmes e episódios',
                filetypes=[('Vídeos','*.mp4 *.mkv *.avi *.mov *.m4v *.ts *.webm'),('Todos','*.*')],parent=root))
        print(json.dumps(result))
    finally:
        root.destroy()

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self,*args):
        pass
    def handle(self):
        try:
            super().handle()
        except OSError:
            pass
    def local(self):
        return self.client_address[0] in ('127.0.0.1','::1')
    def send(self,data,status=200,mime='application/json'):
        if not isinstance(data,bytes):
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        if self.command!='HEAD':
            self.wfile.write(data)
    def do_HEAD(self):
        self.do_GET()
    def do_GET(self):
        if self.path.startswith('/media/'):
            return self.deliver_media()
        if not self.local():
            return self.send({'error':'A interface é acessível somente neste computador.'},403)
        if self.path=='/':
            return self.send((ROOT/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN).encode(),mime='text/html; charset=utf-8')
        if self.path=='/api/library':
            with connect() as db:
                items=[dict(row) for row in db.execute('SELECT * FROM media ORDER BY last_watched DESC,title')]
            with LOCK:
                state=dict(STATE)
            return self.send({'items':items,'tv_ip':setting('tv_ip'),'state':state,
                'tmdb_configured':bool(setting('tmdb_access')),'cover_job':dict(COVER_JOB)})
        if self.path=='/api/health':
            return self.send({'app':'Sala TV'})
        if self.path=='/tmdb-logo.svg':
            return self.send((ROOT/'assets'/'tmdb-logo.svg').read_bytes(),mime='image/svg+xml')
        match=re.fullmatch(r'/cover/([a-f0-9]{20})(?:\?v=\d+)?',self.path)
        if match:
            poster=DATA/'capas'/f'{match[1]}.tmdb.jpg'
            if not poster.is_file(): poster=DATA/'capas'/f'{match[1]}.jpg'
            if poster.is_file():
                image=poster.read_bytes()
                return self.send(image,mime='image/png' if image.startswith(b'\x89PNG') else 'image/jpeg')
        self.send({'error':'Não encontrado'},404)
    def deliver_media(self):
        if self.client_address[0] not in (setting('tv_ip'),'127.0.0.1',pc_ip()):
            return self.send({},403)
        match=re.fullmatch(r'/media/([a-f0-9]{20})/(movie\.[a-z0-9]+)',self.path)
        if not match:
            return self.send({},404)
        try:
            item=get_media(match[1])
            is_sub=match[2]=='movie.srt'
            path=Path(item['subtitle'] or '') if is_sub else Path(item['path'])
            if not is_sub and match[2]!='movie'+path.suffix.lower():
                raise ValueError()
            if not path.is_file():
                raise ValueError()
        except ValueError:
            return self.send({},404)
        content=None
        if is_sub:
            raw=path.read_bytes()
            try: content=raw.decode('utf-8-sig').encode('utf-8')
            except UnicodeDecodeError: content=raw.decode('cp1252').encode('utf-8')
        size=len(content) if content is not None else path.stat().st_size
        first,last=0,size-1
        wanted=self.headers.get('Range')
        if wanted:
            span=re.fullmatch(r'bytes=(\d*)-(\d*)',wanted.strip())
            try:
                if not span or not any(span.groups()): raise ValueError()
                a,b=span.groups()
                first=int(a) if a else max(0,size-int(b))
                last=min(int(b),size-1) if a and b else size-1
                if first>last or first>=size or (not a and int(b)<=0): raise ValueError()
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range',f'bytes */{size}')
                self.send_header('Content-Length','0')
                self.end_headers()
                return
        self.send_response(206 if wanted else 200)
        mime='application/x-subrip; charset=utf-8' if is_sub else media_mime(path)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(last-first+1))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('transferMode.dlna.org','Streaming')
        self.send_header('contentFeatures.dlna.org','DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000')
        if wanted: self.send_header('Content-Range',f'bytes {first}-{last}/{size}')
        if not is_sub and item['subtitle']:
            self.send_header('CaptionInfo.sec',f'http://{pc_ip()}:{PORT}/media/{item["id"]}/movie.srt')
        self.end_headers()
        if self.command=='HEAD': return
        try:
            if content is not None:
                self.wfile.write(content[first:last+1])
            else:
                with path.open('rb') as source:
                    source.seek(first)
                    left=last-first+1
                    while left and not ENDING.is_set():
                        block=source.read(min(65536,left))
                        if not block: break
                        self.wfile.write(block)
                        left-=len(block)
        except OSError:
            pass
    def do_POST(self):
        if not self.local() or self.headers.get('X-Sala-Token')!=TOKEN:
            return self.send({'error':'Pedido não autorizado.'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>100000: raise ValueError('Pedido muito grande.')
            data=json.loads(self.rfile.read(length) or b'{}')
            if self.path=='/api/add':
                paths=data.get('paths',[])
                if data.get('picker'):
                    mode=data['picker']
                    picked=subprocess.run([sys.executable,str(ROOT/'app.py'),'--pick',mode],
                        capture_output=True,text=True,encoding='utf-8',timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
                    if picked.returncode: raise RuntimeError('Não foi possível abrir o seletor de arquivos.')
                    selected=json.loads(picked.stdout)
                    paths=[str(p) for p in Path(selected).rglob('*') if p.suffix.lower() in EXTENSIONS] if mode=='folder' and selected else selected if mode!='folder' else []
                result=[]
                errors=[]
                for path in paths:
                    try: result.append(add_file(path))
                    except Exception as exc: errors.append(str(exc))
                return self.send({'added':result,'errors':errors})
            if self.path=='/api/tmdb/config':
                access=str(data.get('access','')).strip()
                if data.get('clear'):
                    with connect() as db: db.execute("UPDATE settings SET value='' WHERE key='tmdb_access'")
                    return self.send({'ok':True})
                if not access:
                    raise ValueError('Informe sua chave de API ou token de leitura do TMDB.')
                tmdb_covers.api(access,'/configuration')
                protected=tmdb_covers.protect(access)
                with connect() as db: db.execute("UPDATE settings SET value=? WHERE key='tmdb_access'",(protected,))
                update_covers()
                return self.send({'ok':True})
            if self.path=='/api/tmdb/search':
                item=get_media(data['id'])
                results=tmdb_covers.search(tmdb_access(),'tv' if item['kind']=='serie' else 'movie',str(data.get('query') or item['series_name'] or item['title']))
                return self.send({'results':results})
            if self.path=='/api/tmdb/choose':
                kind=data.get('kind')
                if kind not in ('movie','tv'):
                    raise ValueError('Categoria inválida.')
                choose_cover(data['id'],kind,int(data['tmdb_id']))
                return self.send({'ok':True})
            if self.path=='/api/tmdb/update':
                update_covers()
                return self.send({'ok':True})
            if self.path=='/api/play':
                ident=data['id']
                get_media(ident)
                with LOCK:
                    if STATE['pending']: raise ValueError('Aguarde o envio atual.')
                    STATE.pop('preserve_bookmark',None)
                def task():
                    try: play(ident,data.get('resume',True))
                    except Exception as exc:
                        with LOCK: STATE.update(message=str(exc),pending=False)
                threading.Thread(target=task,daemon=True).start()
                return self.send({'ok':True})
            if self.path=='/api/subtitle':
                get_media(data['id'])
                if data.get('picker'):
                    picked=subprocess.run([sys.executable,str(ROOT/'app.py'),'--pick','subtitle'],capture_output=True,
                        text=True,encoding='utf-8',timeout=600,creationflags=subprocess.CREATE_NO_WINDOW)
                    if picked.returncode: raise ValueError('Não foi possível escolher a legenda.')
                    path=json.loads(picked.stdout)
                    if not path: return self.send({'ok':True})
                else: path=data.get('path')
                if path and (not Path(path).is_file() or Path(path).suffix.lower()!='.srt'):
                    raise ValueError('Escolha uma legenda SRT existente.')
                with connect() as db: db.execute('UPDATE media SET subtitle=? WHERE id=?',(path,data['id']))
            elif self.path=='/api/control':
                action=data['action']
                if action not in ('Pause','Play','Stop'): raise ValueError('Comando inválido.')
                with CONTROL:
                    observe()
                    with LOCK:
                        if not STATE['active']: raise ValueError('Nenhum título desta biblioteca está ativo na TV.')
                        current_state=STATE['state']
                    already=(action=='Pause' and current_state=='PAUSED_PLAYBACK') or (action=='Play' and current_state=='PLAYING')
                    if not already:
                        soap(action,{'InstanceID':0,**({'Speed':1} if action=='Play' else {})})
                    if action=='Stop':
                        with LOCK: STATE['active']=None
            elif self.path=='/api/settings':
                address=ipaddress.ip_address(data['tv_ip'])
                if address.version!=4 or not address.is_private: raise ValueError('Use um endereço IPv4 da sua rede local.')
                with LOCK:
                    if STATE['active']: raise ValueError('Pare a reprodução antes de mudar a TV.')
                with connect() as db: db.execute("UPDATE settings SET value=? WHERE key='tv_ip'",(str(address),))
            elif self.path=='/api/edit':
                if data.get('kind') not in ('filme','serie'): raise ValueError('Tipo inválido.')
                title=data['title'].strip()
                if not title: raise ValueError('Informe um título.')
                existing=get_media(data['id'])
                series_name=str(data.get('series_name',existing['series_name'])).strip() if data['kind']=='serie' else ''
                if data['kind']=='serie' and not series_name:
                    series_name=episode_info(Path(existing['path']))[0] or title
                season=int(data.get('season',existing['season']))
                episode=int(data.get('episode',existing['episode']))
                if not 0<=season<=999 or not 0<=episode<=9999:
                    raise ValueError('Informe uma temporada e um episódio válidos.')
                with connect() as db: db.execute('UPDATE media SET title=?,kind=?,series_name=?,season=?,episode=? WHERE id=?',
                    (title,data['kind'],series_name,season,episode,data['id']))
            elif self.path=='/api/remove':
                remove_media(data['id'])
            elif self.path=='/api/shutdown':
                with CONTROL:
                    observe()
                    with LOCK: active=STATE['active']
                    if active:
                        try: soap('Stop',{'InstanceID':0})
                        except Exception: pass
                ENDING.set()
                threading.Thread(target=self.server.shutdown,daemon=True).start()
            else: return self.send({'error':'Não encontrado.'},404)
            self.send({'ok':True})
        except Exception as exc:
            self.send({'error':str(exc)},400)

def run():
    initialize()
    server=http.server.ThreadingHTTPServer(('0.0.0.0',PORT),Handler)
    server.daemon_threads=True
    threading.Thread(target=monitor,daemon=True).start()
    threading.Thread(target=maintenance,daemon=True).start()
    try: server.serve_forever()
    finally:
        ENDING.set()
        with CONTROL: observe()
        server.server_close()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pick',choices=('files','folder','subtitle'))
    parser.add_argument('--launch',action='store_true')
    args=parser.parse_args()
    if args.pick:
        return native_pick(args.pick)
    if args.launch:
        url=f'http://127.0.0.1:{PORT}'
        running=False
        try:
            with OPENER.open(url+'/api/health',timeout=2) as response:
                running=json.load(response).get('app')=='Sala TV'
        except Exception: pass
        if not running:
            log=(DATA/'app.log').open('a',encoding='utf-8')
            child=subprocess.Popen([sys.executable,'-u',str(ROOT/'app.py')],stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
            log.close()
            for _ in range(60):
                if child.poll() is not None: raise RuntimeError('Não foi possível abrir. Consulte dados/app.log.')
                try:
                    with OPENER.open(url+'/api/health',timeout=1) as response:
                        if json.load(response).get('app')=='Sala TV': break
                except Exception: time.sleep(.25)
            else: raise RuntimeError('O programa demorou a iniciar.')
        webbrowser.open(url)
    else: run()

if __name__=='__main__':
    main()
