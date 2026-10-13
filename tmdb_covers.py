"""Cliente TMDB: busca de capas e credencial protegida pelo Windows."""
import base64
import ctypes
from ctypes import wintypes
import difflib
import json
from pathlib import Path
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def protect(value, decrypt=False):
    raw=base64.b64decode(value[6:]) if decrypt else value.encode('utf-8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise RuntimeError('O Windows não conseguiu proteger ou ler a credencial. Configure-a novamente.')
    try:
        data=ctypes.string_at(output.pbData,output.cbData)
        return data.decode('utf-8') if decrypt else 'dpapi:'+base64.b64encode(data).decode()
    finally:
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(output.pbData)

def api(access, endpoint, params=None):
    if not access:
        raise ValueError('Configure a chave ou token do TMDB em Capas e API.')
    if not re.fullmatch(r'/(configuration|search/(movie|tv)|(movie|tv)/\d+)',endpoint):
        raise ValueError('Consulta TMDB inválida.')
    params=dict(params or {})
    headers={'Accept':'application/json','User-Agent':'SalaTV/1.0'}
    if re.fullmatch(r'[a-fA-F0-9]{32}',access):
        params['api_key']=access
    else:
        headers['Authorization']='Bearer '+access
    url='https://api.themoviedb.org/3'+endpoint
    if params:
        url+='?'+urllib.parse.urlencode(params)
    try:
        with OPENER.open(urllib.request.Request(url,headers=headers),timeout=15) as response:
            return json.loads(response.read(2000000))
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):
            raise ValueError('A chave ou token foi recusado pelo TMDB. Confira a credencial nas configurações.') from None
        if exc.code==429:
            raise ValueError('O TMDB limitou as consultas. Aguarde um pouco e tente novamente.') from None
        raise ValueError('O TMDB não respondeu à consulta. Tente novamente mais tarde.') from None
    except (OSError,ValueError,urllib.error.URLError):
        raise ValueError('Não foi possível acessar o TMDB. Confira sua conexão com a internet.') from None

def clean_query(title):
    title=re.sub(r'(?i)S\d+E\d+|\b\d+x\d+\b','',title)
    year=re.search(r'\b((?:19|20)\d{2})\b',title)
    query=re.sub(r'\b(?:19|20)\d{2}\b','',title).strip(' ._-()') if year else title.strip()
    return query,year[1] if year else None

def normalize(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',value).casefold() if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+',' ',value).strip()

def search(access,kind,title):
    query,year=clean_query(title)
    if not query:
        raise ValueError('Informe o nome do filme ou da série.')
    params={'query':query,'language':'pt-BR','include_adult':'false'}
    if year:
        params['year' if kind=='movie' else 'first_air_date_year']=year
    result=api(access,'/search/'+kind,params)
    items=[]
    for row in result.get('results',[]):
        poster=row.get('poster_path')
        if not poster or not re.fullmatch(r'/[A-Za-z0-9_.-]+\.(?:jpg|png)',poster):
            continue
        name=row.get('title') or row.get('name') or ''
        original=row.get('original_title') or row.get('original_name') or name
        score=max(difflib.SequenceMatcher(None,normalize(query),normalize(name)).ratio(),
                  difflib.SequenceMatcher(None,normalize(query),normalize(original)).ratio())
        release=row.get('release_date') or row.get('first_air_date') or ''
        items.append({'id':row['id'],'kind':kind,'title':name,'original':original,'year':release[:4],
            'poster':poster,'preview':'https://image.tmdb.org/t/p/w342'+poster,'score':score})
    items.sort(key=lambda item:item['score'],reverse=True)
    return items[:12]

def automatic_choice(items):
    if not items or items[0]['score']<.9:
        return None
    if len(items)>1 and items[0]['score']-items[1]['score']<.08:
        return None
    return items[0]

def fetch_poster(access,kind,ident,destination):
    details=api(access,f'/{kind}/{int(ident)}',{'language':'pt-BR'})
    poster=details.get('poster_path') or ''
    if not re.fullmatch(r'/[A-Za-z0-9_.-]+\.(?:jpg|png)',poster):
        raise ValueError('Este título não tem uma capa disponível.')
    try:
        with OPENER.open('https://image.tmdb.org/t/p/w500'+poster,timeout=15) as response:
            data=response.read(8000001)
        if len(data)>8000000 or not (data.startswith(b'\xff\xd8') or data.startswith(b'\x89PNG\r\n\x1a\n')):
            raise ValueError()
    except Exception:
        raise ValueError('Não foi possível obter a imagem da capa. Tente novamente.') from None
    destination=Path(destination)
    temp=destination.with_suffix('.tmp')
    temp.write_bytes(data)
    temp.replace(destination)
    return poster
