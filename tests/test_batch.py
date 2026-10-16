"""Batch integration regressions using generated silence and original text only."""
import csv
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from car_music.batch import BatchConverter
from car_music.convert import Options, Result, convert_one, publish_new
from car_music.crypto import AudioError, Library
from car_music.discovery import scan_paths, track_from_path

PROJECT = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root/'output'
        self.library = Library()

    def audio(self, name='Example', directory=None):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/(name+'.wav')
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(44100)
            stream.writeframes(b'\0\0' * 4410)
        return track_from_path(path, self.library, explicit=True)

    def lyric(self, name='Example'):
        path = self.root/(name+'.lrc')
        path.write_bytes(b'[00:01.00]Original test text\r\n')
        return track_from_path(path, self.library, explicit=True)

    def run_batch(self, tracks):
        batch = BatchConverter(list(enumerate(tracks)), Options(self.output, format='original'), self.library, threading.Event())
        return {row: batch.convert(row, track) for row, track in batch.jobs}

    def test_lyrics_follow_audio_even_when_queued_first_and_cache_suffix_is_removed(self):
        name = 'Example-'+'a'*32+'-123-00000000'
        lyric, audio = self.lyric(name), self.audio(name)
        results = self.run_batch([lyric, audio])
        self.assertEqual(Path(results[0].output).stem, 'Example')
        self.assertEqual(Path(results[0].output).stem, Path(results[1].output).stem)

    def test_metadata_rename_and_existing_lyric_share_collision_number(self):
        lyric, audio = self.lyric(), self.audio()
        self.output.mkdir()
        existing = self.output/'Artist - Retitled.lrc'
        existing.write_bytes(b'Keep this existing file')
        with patch('car_music.convert.inspect_tags', return_value={'artist':'Artist', 'title':'Retitled'}):
            results = self.run_batch([lyric, audio])
        self.assertEqual(Path(results[0].output).stem, 'Artist - Retitled (2)')
        self.assertEqual(Path(results[0].output).stem, Path(results[1].output).stem)
        self.assertEqual(existing.read_bytes(), b'Keep this existing file')

    def test_long_paired_name_keeps_collision_suffix(self):
        name = 'A'*110
        lyric, audio = self.lyric(name), self.audio(name)
        self.output.mkdir()
        (self.output/(name+'.lrc')).write_text('Existing', encoding='utf-8')
        results = self.run_batch([lyric, audio])
        self.assertEqual(Path(results[0].output).stem, name+' (2)')
        self.assertEqual(Path(results[0].output).stem, Path(results[1].output).stem)

    def test_hash_pairing_works_when_display_names_differ(self):
        audio = self.audio('a'*32)
        lyric = self.lyric('Different title-'+'a'*32+'-123-00000000')
        results = self.run_batch([lyric, audio])
        self.assertEqual(Path(results[0].output).stem, Path(results[1].output).stem)

    def test_ambiguous_audio_is_not_silently_paired(self):
        a = self.audio(directory=self.root/'a')
        b = self.audio(directory=self.root/'b')
        lyric = self.lyric()
        results = self.run_batch([lyric, a, b])
        self.assertIn('手动确认', results[0].note)

    def test_existing_exact_lyric_target_is_never_overwritten(self):
        self.output.mkdir()
        source = self.root/'temporary.lrc'
        source.write_text('New', encoding='utf-8')
        existing = self.output/'Example.lrc'
        existing.write_text('Old', encoding='utf-8')
        with self.assertRaises(AudioError):
            publish_new(source, self.output, 'Example', 'lrc', exact=True)
        self.assertEqual(existing.read_text(encoding='utf-8'), 'Old')
        self.assertFalse((self.output/'Example (2).lrc').exists())

    def test_same_directory_is_reported_and_nested_output_is_excluded(self):
        lyric = self.lyric()
        tracks, issues = scan_paths([self.root], self.library, excluded=self.root)
        self.assertFalse(tracks)
        self.assertIn('输出目录', issues[0].note)
        self.output.mkdir()
        (self.output/'Already exported.lrc').write_text('[00:00.00]Test', encoding='utf-8')
        tracks, issues = scan_paths([self.root], self.library, excluded=self.output)
        self.assertFalse(issues)
        self.assertEqual([track.path for track in tracks], [lyric.path])

    def test_unreadable_input_is_a_structured_scan_failure(self):
        lyric = self.lyric()
        with patch('car_music.discovery.track_from_path', side_effect=PermissionError('Denied')):
            tracks, issues = scan_paths([lyric.path], self.library, excluded=self.output)
        self.assertFalse(tracks)
        self.assertEqual(issues[0].source, str(lyric.path))
        self.assertIn('Denied', issues[0].note)

    def test_cli_missing_input_sets_failure_exit_and_is_in_csv(self):
        lyric = self.lyric()
        missing = self.root/'missing.krc'
        proc = subprocess.run([sys.executable, str(PROJECT/'main.py'), '--cli', str(lyric.path), str(missing),
                               '--lyrics-only', '--output', str(self.output)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 1, proc.stderr)
        results = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(sorted(result['status'] for result in results), sorted(['成功','失败']))
        self.assertEqual(next(result for result in results if result['status']=='失败')['source'], str(missing))
        with next(self.output.glob('*.csv')).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)

    def test_cli_same_directory_explains_conflict_in_report(self):
        lyric = self.lyric()
        proc = subprocess.run([sys.executable, str(PROJECT/'main.py'), '--cli', str(lyric.path),
                               '--output', str(self.root)], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(proc.returncode, 1)
        self.assertIn('输出目录', proc.stderr)
        self.assertTrue(list(self.root.glob('*.csv')))
        self.assertEqual(lyric.path.read_bytes(), b'[00:01.00]Original test text\r\n')


if __name__ == '__main__':
    unittest.main()
