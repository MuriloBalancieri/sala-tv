"""Prepare TV audio without rewriting the user's source video."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile


CACHE_LIMIT = 12 * 1024**3
CACHE_FILE = re.compile(r'[a-f0-9]{20}-[a-f0-9]{24}\.(?:mp4|mkv)')
SAFE_AUDIO = re.compile(r'^aac \(LC\)(?:\s|,|$)', re.IGNORECASE)
TRACK = re.compile(r'^\s*Stream #0:\d+.*?: Audio: (.+)$', re.MULTILINE)


def cache_entry(cache, ident, source):
    """The key changes when the source changes, even if the library ID stays the same."""
    source = Path(source)
    info = source.stat()
    signature = f'aac-lc-stereo-v1|{source}|{info.st_size}|{info.st_mtime_ns}'
    key = hashlib.sha256(signature.encode()).hexdigest()[:24]
    suffix = '.mp4' if source.suffix.lower() in ('.mp4', '.m4v', '.mov') else '.mkv'
    return cache / f'{ident}-{key}{suffix}', f'movie.{key}{suffix}'


def needs_conversion(output):
    input_info = output.split('Output #', 1)[0]
    tracks = TRACK.findall(input_info)
    for track in tracks:
        if not SAFE_AUDIO.match(track) or not re.search(r',\s*(?:44100|48000) Hz,\s*(?:mono|stereo)\s*,', track):
            return True
    return False


def run_ffmpeg(encoder, args, timeout):
    try:
        result = subprocess.run([str(encoder), '-hide_banner', '-nostdin', *args],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('O preparo do áudio excedeu o tempo limite.') from exc
    except OSError as exc:
        raise RuntimeError('Não foi possível abrir o FFmpeg para preparar o áudio.') from exc
    if result.returncode:
        print('FFmpeg (áudio para TV):', result.stderr[-1500:], flush=True)
        raise RuntimeError('Não foi possível preparar o áudio deste vídeo para a TV.')
    return result.stderr


def prune_cache(cache, keep, active=None):
    candidates = []
    for path in cache.iterdir():
        if CACHE_FILE.fullmatch(path.name) and path.is_file():
            info = path.stat()
            candidates.append((info.st_mtime_ns, info.st_size, path))
    total = sum(size for _, size, _ in candidates)
    for _, size, path in sorted(candidates):
        if total <= CACHE_LIMIT:
            break
        if path == keep or (active and path.name.startswith(active+'-')):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        total -= size


def discard(cache, ident):
    """Remove as copias convertidas de um titulo; devolve quantas continuaram em uso."""
    if not re.fullmatch(r'[a-f0-9]{20}', ident):
        return 0
    try:
        entries = list(Path(cache).iterdir())
    except OSError:
        return 0
    remaining = 0
    for path in entries:
        if not (CACHE_FILE.fullmatch(path.name) and path.name.startswith(ident + '-')):
            continue
        try:
            path.unlink()
        except OSError:
            remaining += 1
    return remaining


def prepare(encoder, cache, ident, source, on_convert=lambda: None, active=None):
    source = Path(source)
    original = (source, 'movie' + source.suffix.lower())
    if not encoder:
        raise RuntimeError('FFmpeg não encontrado. Instale as dependências com Instalar.cmd ou defina SALA_TV_FFMPEG para verificar e adaptar o áudio.')
    target, name = cache_entry(cache, ident, source)
    if target.is_file() and target.stat().st_size > 0:
        try:
            target.touch()
        except OSError:
            pass
        return target, name
    probe = run_ffmpeg(encoder, ['-i', str(source), '-map', '0:v:0', '-map', '0:a?',
                                 '-t', '0', '-c', 'copy', '-f', 'null', '-'], 25)
    if not needs_conversion(probe):
        return original
    on_convert()
    cache.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.audio-', suffix=target.suffix, dir=cache)
    os.close(fd)
    temporary = Path(tmp)
    try:
        args = ['-v', 'error', '-xerror', '-y', '-i', str(source), '-map', '0:v:0', '-map', '0:a',
                '-map', '0:s?', '-c:v', 'copy', '-c:s', 'copy', '-c:a', 'aac', '-profile:a', 'aac_low',
                '-ac', '2', '-ar', '48000', '-b:a', '192k']
        if target.suffix == '.mp4':
            args += ['-movflags', '+faststart']
        run_ffmpeg(encoder, [*args, str(temporary)], 1800)
        if not temporary.stat().st_size or cache_entry(cache, ident, source)[0] != target:
            raise RuntimeError('O vídeo mudou durante o preparo. Tente enviá-lo novamente.')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        prune_cache(cache, target, active)
    except OSError:
        pass  # Uma falha de limpeza não impede a reprodução de uma cópia pronta.
    return target, name
