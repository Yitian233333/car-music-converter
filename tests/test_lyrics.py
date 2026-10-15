"""Synthetic lyrics only. No private music, account data or copyrighted lyrics."""
import base64
import json
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from car_music.convert import Options, convert_one
from car_music.crypto import AudioError, Cancelled, Library
from car_music.discovery import scan_paths, track_from_path
from car_music.lyrics import decode_krc, parse_lyrics, read_text, render_lyrics, timestamp


def fixture(text):
    # Independent numeric format key, used to construct synthetic input only.
    key = [64, 71, 97, 119, 94, 50, 116, 71, 81, 54, 49, 45, 206, 210, 110, 105]
    packed = zlib.compress(text.encode("utf-8"))
    return b"krc1" + bytes(value ^ key[index % 16] for index, value in enumerate(packed))


class LyricTests(unittest.TestCase):
    def test_krc_words_translation_offset_and_rounding(self):
        language = base64.b64encode(json.dumps({"content": [{"type": 1, "lyricContent": [["你好世界"]]}]}).encode()).decode()
        raw = f"[ti:Example]\n[offset:-20]\n[language:{language}]\n[59995,1500]<0,500,0>Hello <500,1000,0>world\n"
        text = decode_krc(fixture(raw))
        result, extension, _ = render_lyrics(parse_lyrics(text, ".krc"), "lrc")
        self.assertEqual(extension, "lrc")
        self.assertEqual(result, "[ti:Example]\n[offset:-20]\n[01:00.00]Hello world / 你好世界\n")

    def test_corrupt_or_truncated_krc_is_rejected(self):
        data = fixture("[0,1000]<0,1000,0>Example\n")
        for broken in (b"bad!" + data[4:], data[:-2], data + b"trailing"):
            with self.subTest(broken=broken[:4]), self.assertRaises(AudioError):
                decode_krc(broken)

    def test_decompression_is_bounded(self):
        data = fixture("A" * 2000)
        with patch("car_music.lyrics.MAX_LYRIC_BYTES", 1000), self.assertRaises(AudioError):
            decode_krc(data)

    def test_lrc_multi_timestamps_encoding_and_offset(self):
        data = "[offset:+25]\n[00:01.2][01:02.345]自制测试\n".encode("gb18030")
        doc = parse_lyrics(read_text(data, ".lrc"), ".lrc")
        self.assertEqual([line.milliseconds for line in doc.lines], [1200, 62345])
        self.assertIn("[offset:+25]", render_lyrics(doc, "lrc")[0])
        self.assertEqual(timestamp(62345), "[01:02.35]")

    def test_bad_translation_keeps_original_and_reports_it(self):
        doc = parse_lyrics("[language:!bad!]\n[0,1]<0,1,0>Example", ".krc")
        text, _, warning = render_lyrics(doc, "lrc")
        self.assertIn("Example", text)
        self.assertTrue(warning)

    def test_plain_text_does_not_get_invented_timestamps(self):
        doc = parse_lyrics("自制测试\nExample", ".txt")
        text, extension, note = render_lyrics(doc, "lrc")
        self.assertEqual(extension, "txt")
        self.assertNotIn("[00:", text)
        self.assertTrue(note)

    def test_malformed_timing_never_saves_partial_lyrics(self):
        for text, extension in (("[00:99.00]Example", ".lrc"), ("[0,10]Example\nbroken line", ".krc")):
            with self.subTest(extension=extension), self.assertRaises(AudioError):
                parse_lyrics(text, extension)

    def test_batch_discovery_no_overwrite_cancellation_and_no_ffmpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Lyric"
            source.mkdir()
            krc = source / ("Example-" + "a" * 32 + "-123-00000000.krc")
            original = fixture("[1000,1000]<0,1000,0>Example")
            krc.write_bytes(original)
            (source / "Plain.txt").write_text("Example", encoding="utf-8")
            (root / "README.txt").write_text("Unrelated documentation", encoding="utf-8")
            output = root / "out"
            tracks, errors = scan_paths([root], Library(), excluded=output)
            self.assertFalse(errors)
            self.assertEqual(len(tracks), 2)
            track = track_from_path(krc, Library())
            self.assertEqual(track.name, "Example")
            with patch("car_music.convert.run_ffmpeg", side_effect=AssertionError("Lyrics must not use FFmpeg")):
                a = convert_one(track, Options(output), Library(), threading.Event())
                b = convert_one(track, Options(output), Library(), threading.Event())
                plain = convert_one(track, Options(output, lyric_format="txt"), Library(), threading.Event())
            self.assertNotEqual(a.output, b.output)
            self.assertEqual(Path(a.output).read_text(encoding="utf-8"), "[00:01.00]Example\n")
            self.assertEqual(Path(plain.output).read_text(encoding="utf-8"), "Example\n")
            self.assertEqual(krc.read_bytes(), original)
            stop = threading.Event()
            def cancel_during_save(percent, message):
                if percent >= 70:
                    stop.set()
            before = set(output.iterdir())
            with self.assertRaises(Cancelled):
                convert_one(track, Options(output), Library(), stop, cancel_during_save)
            self.assertEqual(set(output.iterdir()), before)
            self.assertFalse(list(output.glob(".car-music-*")))


if __name__ == "__main__":
    unittest.main()
