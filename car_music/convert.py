"""Streaming conversion, validation and atomic publication of completed audio."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .crypto import AudioError, Cancelled, Library, decoder_for, sniff
from .discovery import Track
from .lyrics import LYRIC_EXTENSIONS, MAX_LYRIC_BYTES, parse_lyrics, read_text, render_lyrics

CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


@dataclass(frozen=True)
class Options:
    output: Path
    format: str = "mp3"
    bitrate: int = 320
    car_mode: bool = True
    lyric_format: str = "lrc"


@dataclass
class Result:
    source: str
    status: str
    output: str = ""
    original_format: str = ""
    note: str = ""
    seconds: float = 0.0


def ffmpeg_path() -> str:
    # Packaged alongside the application; no runtime dependency download.
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    for path in (base / "tools/ffmpeg.exe", base / "tools/ffmpeg"):
        if path.is_file():
            return str(path)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(bundled).is_file():
            return bundled
    except (ImportError, RuntimeError):
        pass
    raise AudioError("未找到 FFmpeg，请完整解压应用目录，保留 _internal 文件夹。")


def safe_stem(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:110]
    if not name:
        name = "音频"
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name, re.I):
        name = "_" + name
    return name


def check_cancel(stop: threading.Event):
    if stop.is_set():
        raise Cancelled("已取消；原文件和已经完成的输出均已保留。")


def run_ffmpeg(args: list[str], stop: threading.Event, update: Callable[[str], None] | None = None) -> str:
    """Drain stdout on another thread so cancellation never waits for a new line."""
    lines = []
    proc = subprocess.Popen([ffmpeg_path(), "-hide_banner", "-nostdin", "-protocol_whitelist", "file,pipe", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)

    def drain():
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace").strip()
            lines.append(line)
            if update and line.startswith("out_time="):
                update(line.partition("=")[2])
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            check_cancel(stop)
            time.sleep(0.08)
        reader.join(timeout=3)
        check_cancel(stop)
        if proc.returncode:
            detail = "\n".join(lines[-8:])[-1400:]
            raise AudioError("FFmpeg 无法完整读取或转换这份音频，缓存可能不完整。\n" + detail)
        return "\n".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=3)
        if proc.stdout:
            proc.stdout.close()


def inspect_tags(path: Path, stop: threading.Event) -> dict:
    # A short decode probes metadata and duration without requiring a separate ffprobe.
    text = run_ffmpeg(["-v", "info", "-i", str(path), "-map", "0:a:0", "-t", "0.01", "-f", "null", "-"], stop)
    tags = {}
    for key in ("title", "artist", "album"):
        match = re.search(r"^\s*" + key + r"\s*:\s*(.+)$", text, re.M | re.I)
        if match:
            tags[key] = match.group(1).strip()
    match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
    if match:
        tags["duration"] = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return tags


def publish_new(source: Path, directory: Path, stem: str, extension: str) -> Path:
    """Create a new name atomically. Never overwrite any existing user file."""
    for index in range(1, 100000):
        name = f"{stem}.{extension}" if index == 1 else f"{stem} ({index}).{extension}"
        dest = directory / name
        try:
            # Both files are on the same volume. Hard-link creation is atomic and no-clobber.
            os.link(source, dest)
            return dest
        except FileExistsError:
            continue
        except OSError:
            # exFAT/FAT32 do not support hard links. Windows rename is also no-clobber.
            if os.name == "nt":
                try:
                    os.rename(source, dest)
                    return dest
                except FileExistsError:
                    continue
            raise AudioError("无法保存输出文件，请选择可写入的本地目录并检查剩余空间。")
    raise AudioError("同名文件过多，请更换输出目录。")


def convert_one(track: Track, options: Options, library: Library, stop: threading.Event,
                progress: Callable[[int, str], None] = lambda percent, message: None) -> Result:
    if track.path.suffix.lower() in LYRIC_EXTENSIONS:
        return convert_lyric(track, options, stop, progress)
    started = time.monotonic()
    check_cancel(stop)
    if options.format not in ("mp3", "flac", "original") or options.bitrate not in (128, 192, 256, 320):
        raise AudioError("输出格式或 MP3 码率无效。")
    output = options.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    original_stat = track.path.stat()
    with track.path.open("rb") as src:
        header = src.read(4096)
        offset, cipher, _ = decoder_for(header, original_stat.st_size, library, track.path.stem)
        total = original_stat.st_size - offset
        src.seek(offset)
        # Work files stay on the output volume, then disappear on success/failure/cancel.
        with tempfile.TemporaryDirectory(prefix=".car-music-", dir=output) as temporary:
            work = Path(temporary)
            decoded = work / "decoded.audio"
            source_format, position = "", 0
            with decoded.open("wb") as target:
                while True:
                    check_cancel(stop)
                    data = src.read(256 * 1024)
                    if not data:
                        break
                    data = cipher.decrypt(data, position) if cipher else data
                    if position == 0:
                        source_format = sniff(data)
                        if not source_format:
                            raise AudioError("解密结果未通过音频文件头校验。可能是未知缓存格式、密钥不匹配或文件不完整。")
                    target.write(data)
                    position += len(data)
                    progress(int(position / total * 30), "正在解密" if cipher else "正在读取")
            current_stat = track.path.stat()
            if original_stat.st_size != current_stat.st_size or original_stat.st_mtime_ns != current_stat.st_mtime_ns:
                raise AudioError("源文件在转换期间发生变化，请等酷狗下载完成后重试。")
            if not position:
                raise AudioError("音频内容为空。")
            tags = inspect_tags(decoded, stop)
            duration = tags.get("duration", 0)
            stem = track.name
            if tags.get("title"):
                stem = f"{tags['artist']} - {tags['title']}" if tags.get("artist") else tags["title"]
            stem = safe_stem(stem)
            destination_format = source_format if options.format == "original" else options.format
            note = ""
            if options.format == "flac" and source_format not in ("flac", "wav"):
                note = f"源音频为 {source_format.upper()}；FLAC 封装不会恢复已经损失的音质。"
            # Original mode is byte-preserving. MP3 can be copied when no car resampling was requested.
            do_copy = options.format == "original" or (options.format == source_format == "flac" and not options.car_mode)
            if do_copy:
                final = decoded
                progress(40, "正在验证完整音频")
            else:
                final = work / f"converted.{destination_format}"
                args = ["-v", "error", "-xerror", "-y", "-i", str(decoded), "-map", "0:a:0", "-map_metadata", "0", "-vn"]
                if options.format == "mp3":
                    args += ["-c:a", "libmp3lame", "-b:a", f"{options.bitrate}k", "-id3v2_version", "3", "-write_id3v1", "1"]
                else:
                    args += ["-c:a", "flac", "-compression_level", "5"]
                if options.car_mode:
                    args += ["-ar", "44100", "-ac", "2"]
                    if options.format == "flac":
                        args += ["-sample_fmt", "s16"]
                args += ["-progress", "pipe:1", "-nostats", str(final)]

                def on_time(value: str):
                    try:
                        h, m, s = map(float, value.split(":"))
                        fraction = min(1, (h * 3600 + m * 60 + s) / duration) if duration else 0
                        progress(30 + int(fraction * 55), "正在编码")
                    except ValueError:
                        pass
                run_ffmpeg(args, stop, on_time)
            # Decode the entire original and output, with strict error handling; magic alone is insufficient.
            # The encode path has already fully decoded the original with -xerror.
            progress(90, "正在验证完整音频")
            run_ffmpeg(["-v", "error", "-xerror", "-i", str(final), "-map", "0:a:0", "-f", "null", "-"], stop)
            if final.stat().st_size < 16:
                raise AudioError("输出文件为空，未保存。")
            check_cancel(stop)
            destination = publish_new(final, output, stem, destination_format)
            progress(100, "已完成")
    return Result(str(track.path), "成功", str(destination), source_format, note, round(time.monotonic() - started, 2))


def convert_lyric(track: Track, options: Options, stop: threading.Event,
                  progress: Callable[[int, str], None]) -> Result:
    started = time.monotonic()
    check_cancel(stop)
    before = track.path.stat()
    if before.st_size > MAX_LYRIC_BYTES:
        raise AudioError("歌词文件过大，未处理。")
    progress(10, "正在解码歌词")
    with track.path.open("rb") as stream:
        text = read_text(stream.read(MAX_LYRIC_BYTES + 1), track.path.suffix.lower())
    document = parse_lyrics(text, track.path.suffix.lower())
    content, extension, note = render_lyrics(document, options.lyric_format)
    after = track.path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise AudioError("歌词文件在转换期间发生变化，请等下载完成后重试。")
    check_cancel(stop)
    output = options.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    progress(70, "正在保存歌词")
    with tempfile.TemporaryDirectory(prefix=".car-music-", dir=output) as temporary:
        final = Path(temporary) / ("lyrics." + extension)
        final.write_text(content, encoding="utf-8", newline="\r\n")
        saved = final.read_text(encoding="utf-8")
        if saved != content or (extension == "lrc" and not parse_lyrics(saved, ".lrc").lines):
            raise AudioError("输出歌词完整性验证失败，未保存。")
        check_cancel(stop)
        destination = publish_new(final, output, safe_stem(track.name), extension)
    progress(100, "已完成")
    return Result(str(track.path), "成功", str(destination), track.path.suffix[1:], note, round(time.monotonic() - started, 2))
