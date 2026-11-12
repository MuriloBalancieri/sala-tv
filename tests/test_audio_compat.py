from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import app
import audio_compat


class AudioCompatibilityTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'work'
        scratch.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source.mp4'
        self.cache = self.root / 'cache'
        self.ident = 'a' * 20

    def fixture(self, sample_rate):
        encoder = app.ffmpeg()
        if not encoder:
            self.skipTest('FFmpeg não instalado')
        subprocess.run([str(encoder), '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-f', 'lavfi', '-i', 'color=size=160x90:rate=24:duration=1',
                        '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate={sample_rate}:duration=1',
                        '-c:v', 'libx264', '-c:a', 'aac', str(self.source)],
                       capture_output=True, timeout=15, check=True)
        return encoder

    def test_incompatible_audio_is_converted_without_changing_video_or_source(self):
        encoder = self.fixture(96000)
        original = self.source.read_bytes()
        seen = []
        converted, name = audio_compat.prepare(encoder, self.cache, self.ident, self.source,
                                               on_progress=seen.append, duration=1)
        self.assertEqual(seen[0], 0.0)
        self.assertEqual(seen[-1], 1.0)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in seen))
        self.assertNotEqual(converted, self.source)
        self.assertEqual(self.source.read_bytes(), original)
        probe = audio_compat.run_ffmpeg(encoder, ['-i', str(converted), '-t', '0', '-f', 'null', '-'], 15)
        self.assertRegex(probe, r'Audio: aac \(LC\).+48000 Hz, stereo')
        def video_hash(path):
            return subprocess.check_output([str(encoder), '-v', 'error', '-i', str(path),
                                            '-map', '0:v:0', '-c', 'copy', '-f', 'hash', '-'], timeout=15)
        self.assertEqual(video_hash(converted), video_hash(self.source))
        with patch.object(audio_compat, 'run_ffmpeg', side_effect=AssertionError('Cache deveria ser reutilizado')):
            self.assertEqual(audio_compat.prepare(encoder, self.cache, self.ident, self.source), (converted, name))
        self.source.write_bytes(original+b'x')
        self.assertNotEqual(audio_compat.cache_entry(self.cache, self.ident, self.source)[1], name)

    def test_compatible_aac_is_sent_without_creating_a_copy(self):
        encoder = self.fixture(48000)
        self.assertEqual(audio_compat.prepare(encoder, self.cache, self.ident, self.source),
                         (self.source, 'movie.mp4'))
        self.assertFalse(self.cache.exists())

    def test_discard_removes_only_that_titles_conversions(self):
        self.cache.mkdir()
        other_id = 'b' * 20
        keep = self.cache / f'{other_id}-{"c"*24}.mp4'
        keep.write_bytes(b'x')
        lookalike = self.cache / f'{self.ident}-not-a-key.mp4'
        lookalike.write_bytes(b'x')
        target = self.cache / f'{self.ident}-{"d"*24}.mkv'
        target.write_bytes(b'x')
        self.assertEqual(audio_compat.discard(self.cache, self.ident), 0)
        self.assertFalse(target.exists())
        self.assertTrue(keep.exists())
        self.assertTrue(lookalike.exists())
        self.assertEqual(audio_compat.discard(self.cache, '../fora'), 0)
        self.assertTrue(keep.exists())
        self.assertEqual(audio_compat.discard(self.root / 'inexistente', self.ident), 0)

    def test_missing_ffmpeg_is_reported_instead_of_sending_unchecked_audio(self):
        self.source.write_bytes(b'unchanged')
        with self.assertRaisesRegex(RuntimeError, 'FFmpeg'):
            audio_compat.prepare(None, self.cache, self.ident, self.source)
        self.assertEqual(list(self.cache.iterdir()) if self.cache.exists() else [], [])
        self.assertEqual(self.source.read_bytes(), b'unchanged')

    def test_failed_conversion_does_not_leave_a_playable_cache_entry(self):
        self.source.write_bytes(b'unchanged')
        probe = '  Stream #0:1: Audio: aac (LC), 96000 Hz, 5.1, fltp'
        with patch.object(audio_compat,'run_ffmpeg',side_effect=[probe, RuntimeError('Falha no FFmpeg')]):
            with self.assertRaisesRegex(RuntimeError, 'Falha no FFmpeg'):
                audio_compat.prepare('ffmpeg', self.cache, self.ident, self.source)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b'unchanged')
