"""Shared batch ordering and conservative audio/lyric filename pairing."""
from __future__ import annotations

import re
import threading
import unicodedata
from pathlib import Path

from .convert import Options, Result, convert_one
from .crypto import Library
from .discovery import Track
from .lyrics import CACHE_SUFFIX, LYRIC_EXTENSIONS, lyric_stem


def name_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", lyric_stem(value)).casefold()
    return ''.join(character for character in value if character.isalnum())


def hash_keys(track: Track) -> set[str]:
    values = {track.path.stem}
    suffix = CACHE_SUFFIX.search(track.path.stem)
    if suffix:
        values.add(suffix[0].split('-')[1])
    values.update(str(track.metadata.get(key) or '') for key in ('FileHash', 'Hash', 'MD5', 'OriginalHash'))
    return {value.lower() for value in values if re.fullmatch(r'[0-9a-fA-F]{16,64}', value)}


class BatchConverter:
    def __init__(self, jobs: list[tuple[int, Track]], options: Options, library: Library, stop: threading.Event):
        self.options, self.library, self.stop = options, library, stop
        self.audio = [(row, track) for row, track in jobs if track.path.suffix.lower() not in LYRIC_EXTENSIONS]
        self.lyrics = [(row, track) for row, track in jobs if track.path.suffix.lower() in LYRIC_EXTENSIONS]
        # Audio must finish before lyrics can follow its actual published name,
        # including metadata-based renaming and any collision number.
        self.jobs = self.audio + self.lyrics
        self.outputs: dict[int, Result] = {}

    def match(self, lyric: Track) -> int | None:
        hashes = hash_keys(lyric)
        by_hash = [row for row, track in self.audio if hashes & hash_keys(track)]
        if by_hash:
            return by_hash[0] if len(by_hash) == 1 else None
        keys = {name_key(lyric.name), name_key(lyric.path.stem)} - {''}
        matches = []
        for row, track in self.audio:
            names = {name_key(track.name), name_key(track.path.stem)}
            result = self.outputs.get(row)
            if result and result.output:
                names.add(name_key(Path(result.output).stem))
            if keys & names:
                matches.append(row)
        return matches[0] if len(matches) == 1 else None

    def convert(self, row: int, track: Track, progress=lambda percent, message: None) -> Result:
        if track.path.suffix.lower() not in LYRIC_EXTENSIONS:
            # Leave both possible lyric filenames free, so a paired export never
            # needs a different collision number from its audio.
            companions = ('lrc', 'txt') if self.lyrics else ()
            result = convert_one(track, self.options, self.library, self.stop, progress, companions=companions)
            self.outputs[row] = result
            return result
        match = self.match(track)
        audio = self.outputs.get(match) if match is not None else None
        if audio and audio.status == '成功':
            result = convert_one(track, self.options, self.library, self.stop, progress,
                                 output_stem=Path(audio.output).stem, exact=True)
        else:
            result = convert_one(track, self.options, self.library, self.stop, progress)
            if self.audio:
                warning = '未找到唯一且转换成功的配对音频；播放前请手动确认歌词与音乐同名。'
                result.note = (result.note + ' ' + warning).strip()
        return result
