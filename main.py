from __future__ import annotations

import argparse
import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="随行音乐转换器：本地批量转换音频及 KRC / LRC / TXT 歌词")
    parser.add_argument("inputs", nargs="*", type=Path)
    parser.add_argument("--cli", action="store_true", help="使用命令行；默认打开桌面窗口")
    parser.add_argument("--scan-kugou", action="store_true", help="扫描本机酷狗默认目录")
    parser.add_argument("--output", type=Path, default=Path("已转换"))
    parser.add_argument("--format", choices=["mp3", "flac", "original"], default="mp3")
    parser.add_argument("--lyric-format", choices=["lrc", "txt"], default="lrc", help="歌词输出格式，与音频格式独立；默认 LRC")
    parser.add_argument("--lyrics-only", action="store_true", help="只处理 KRC / LRC / TXT 歌词")
    parser.add_argument("--bitrate", type=int, choices=[128, 192, 256, 320], default=320)
    parser.add_argument("--car", action="store_true", help="44.1 kHz 双声道；FLAC 16 bit")
    parser.add_argument("--database", default="", help="自定义本机 KGMusicV3.db 路径")
    args = parser.parse_args()
    if args.cli:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    if not args.cli:
        from car_music.ui import run_gui
        return run_gui(args.inputs)

    from car_music.convert import Options, Result, convert_one
    from car_music.crypto import Cancelled
    from car_music.discovery import load_library, music_roots, scan_paths
    from car_music.reports import export_results
    import time

    library = load_library(args.database)
    paths = music_roots() if args.scan_kugou else args.inputs
    if not paths:
        parser.error("请给出输入文件或文件夹，或使用 --scan-kugou。")
    tracks, errors = scan_paths(paths, library, excluded=args.output.resolve())
    if args.lyrics_only:
        from car_music.lyrics import LYRIC_EXTENSIONS
        tracks = [track for track in tracks if track.path.suffix.lower() in LYRIC_EXTENSIONS]
    options = Options(args.output, args.format, args.bitrate, args.car, args.lyric_format)
    stop = threading.Event()
    results = []
    for error in library.errors + errors:
        print(error, file=sys.stderr)
    try:
        for track in tracks:
            try:
                result = convert_one(track, options, library, stop)
            except Exception as exc:
                result = Result(str(track.path), "失败", note=str(exc))
            results.append(result)
            print(json.dumps(asdict(result), ensure_ascii=False))
    except KeyboardInterrupt:
        stop.set()
        return 130
    finally:
        if results:
            args.output.mkdir(parents=True, exist_ok=True)
            export_results(args.output / f"转换报告-{time.time_ns()}.csv", results)
    if not tracks:
        print("未发现可处理的音频或歌词文件。", file=sys.stderr)
        return 2
    return 1 if any(r.status != "成功" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
