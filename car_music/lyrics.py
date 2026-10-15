"""Local KRC decoding and lyric export. Never writes to the input file.

KRC format reference: https://github.com/kangkang520/kugou-lyric
Decoding uses Python's standard library; no account, database or FFmpeg.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass

from .crypto import AudioError

LYRIC_EXTENSIONS = {".krc", ".lrc", ".txt"}
KRC_KEY = bytes.fromhex("404761775e3274475136312dced26e69")
MAX_LYRIC_BYTES = 16 * 1024 * 1024
CACHE_SUFFIX = re.compile(r"-[0-9a-f]{16,64}-\d+-[0-9a-f]{8}$", re.I)
KRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
WORD_TIME = re.compile(r"<\d+,\d+,-?\d+>|<\d+:\d{1,2}(?:\.\d{1,3})?>")
LRC_TIME = re.compile(r"\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]")
META = re.compile(r"^\[([a-zA-Z][\w-]*):(.*)\]$")
PUBLIC_TAGS = {"ti", "ar", "al", "by", "offset", "au", "re", "ve", "length"}


@dataclass(frozen=True)
class Line:
    milliseconds: int
    text: str
    translation: str = ""


@dataclass
class Lyrics:
    metadata: list[str]
    lines: list[Line]
    plain: str = ""
    warning: str = ""


def lyric_stem(name: str) -> str:
    return CACHE_SUFFIX.sub("", name) or name


def decode_krc(data: bytes) -> str:
    if not data.startswith(b"krc1"):
        raise AudioError("KRC 文件头无效；文件可能损坏或不是酷狗 KRC 歌词。")
    if len(data) > MAX_LYRIC_BYTES:
        raise AudioError("歌词文件过大，未处理。")
    compressed = bytes(value ^ KRC_KEY[i % 16] for i, value in enumerate(data[4:]))
    try:
        decoder = zlib.decompressobj()
        plain = decoder.decompress(compressed, MAX_LYRIC_BYTES + 1)
        if len(plain) > MAX_LYRIC_BYTES or decoder.unconsumed_tail:
            raise AudioError("歌词解压后的内容过大，未处理。")
        if not decoder.eof or decoder.unused_data:
            raise AudioError("KRC 压缩数据不完整或含有额外数据，未保存输出。")
        return plain.decode("utf-8-sig")
    except (zlib.error, UnicodeError) as exc:
        raise AudioError("KRC 歌词解码失败；压缩数据损坏或文字编码无效。") from exc


def read_text(data: bytes, extension: str) -> str:
    if len(data) > MAX_LYRIC_BYTES:
        raise AudioError("歌词文件过大，未处理。")
    if extension == ".krc":
        return decode_krc(data)
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        # Only try UTF-16 when an explicit BOM is present.
        if encoding == "utf-16" and data[:2] not in (b"\xff\xfe", b"\xfe\xff"):
            continue
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise AudioError("无法识别歌词文字编码，支持 UTF-8、带 BOM 的 UTF-16 和 GB18030。")


def parse_lyrics(text: str, extension: str) -> Lyrics:
    if "\0" in text:
        raise AudioError("歌词含有无效的空字符。")
    if extension == ".txt":
        if not text.strip():
            raise AudioError("歌词内容为空。")
        return Lyrics([], [], plain=text.strip())
    metadata, lines, language = [], [], ""
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = KRC_LINE.fullmatch(raw) if extension == ".krc" else None
        if match:
            lines.append(Line(int(match[1]), WORD_TIME.sub("", match[3])))
            continue
        if extension == ".lrc":
            tags = list(LRC_TIME.finditer(raw))
            if tags and tags[0].start() == 0:
                end = 0
                for tag in tags:
                    if tag.start() != end:
                        break
                    if int(tag[2]) >= 60:
                        raise AudioError("LRC 的秒数时间标签无效。")
                    end = tag.end()
                leading = [tag for tag in tags if tag.end() <= end]
                payload = WORD_TIME.sub("", raw[end:])
                for tag in leading:
                    ms = (int(tag[1]) * 60 + int(tag[2])) * 1000 + int((tag[3] or "").ljust(3, "0"))
                    lines.append(Line(ms, payload))
                continue
        match = META.fullmatch(raw)
        if match:
            key, value = match[1].lower(), match[2]
            if key == "language":
                language = value
            elif key in PUBLIC_TAGS:
                if key == "offset" and not re.fullmatch(r"[+-]?\d+", value.strip()):
                    raise AudioError("歌词 offset 时间偏移无效。")
                metadata.append(f"[{key}:{value}]")
            continue
        raise AudioError("存在无法解析的歌词行或时间标签，未保存不完整结果。")
    if not lines or not any(line.text.strip() for line in lines):
        raise AudioError("未找到含文字的同步歌词行。")
    warning = ""
    if language:
        try:
            obj = json.loads(base64.b64decode(language, validate=True))
            candidates = [track for track in obj.get("content", []) if track.get("type") == 1]
            if candidates:
                translated = candidates[0].get("lyricContent", [])
                if len(translated) != len(lines) or not all(isinstance(row, list) and all(isinstance(t, str) for t in row) for row in translated):
                    raise ValueError("translation alignment")
                lines = [Line(line.milliseconds, line.text, "".join(row).strip()) for line, row in zip(lines, translated)]
        except (ValueError, TypeError, AttributeError, KeyError):
            warning = "翻译附加数据无法对齐或已损坏，已保留原文歌词。"
    return Lyrics(metadata, lines, warning=warning)


def timestamp(milliseconds: int) -> str:
    # Standard centisecond LRC, rounded with carry across seconds/minutes.
    minutes, rest = divmod((milliseconds + 5) // 10, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]"


def render_lyrics(lyrics: Lyrics, output_format: str) -> tuple[str, str, str]:
    if output_format not in ("lrc", "txt"):
        raise AudioError("歌词输出格式无效，请选择 LRC 或 TXT。")
    if lyrics.plain:
        # Plain text has no timing information; never invent synchronized times.
        note = "原文件为纯文本，无时间轴，已保留为 TXT。" if output_format == "lrc" else ""
        return lyrics.plain + "\n", "txt", note
    body = []
    for line in sorted(lyrics.lines, key=lambda item: item.milliseconds):
        text = line.text
        if line.translation and line.translation != text.strip():
            text += " / " + line.translation
        body.append((timestamp(line.milliseconds) if output_format == "lrc" else "") + text)
    if output_format == "lrc":
        body = lyrics.metadata + body
    return "\n".join(body) + "\n", output_format, lyrics.warning
