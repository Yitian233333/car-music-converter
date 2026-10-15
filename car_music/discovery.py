from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .crypto import KGM_MAGIC, VPR_MAGIC, Library, sniff

EXTENSIONS = {".kgm", ".kgma", ".kgg", ".kge", ".vpr", ".flac", ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".wma"}
HASH_NAME = re.compile(r"^[0-9a-f]{32}$", re.I)


@dataclass
class Track:
    path: Path
    name: str
    kind: str
    size: int
    metadata: dict = field(default_factory=dict)


def database_candidates(errors: list[str] | None = None) -> list[Path]:
    dirs = [Path.cwd()]
    for var in ("APPDATA", "LOCALAPPDATA", "PUBLIC"):
        if os.environ.get(var):
            base = Path(os.environ[var])
            dirs.extend([base / "KuGou8", base / "KuGou", base / "KuGou/KGMusic"])
    candidates = []
    for directory in dict.fromkeys(dirs):
        path = directory / "KGMusicV3.db"
        try:
            if path.is_file():
                candidates.append(path)
        except OSError:
            # A blocked redirected profile or unavailable drive must not prevent
            # KGM conversion or selecting another database in the application.
            if errors is not None:
                errors.append(f"无法访问密钥数据库：{path}。可手动选择另一份 KGMusicV3.db。")
    return candidates


def load_library(custom: str = "") -> Library:
    library = Library()
    paths = [Path(custom)] if custom else database_candidates(library.errors)
    for path in paths:
        try:
            library.load(path)
        except Exception as exc:
            library.errors.append(f"{path.name}：{exc}")
    return library


def music_roots() -> list[Path]:
    paths = []
    for db in database_candidates():
        ini = db.with_name("KuGou.ini")
        if ini.is_file():
            data = ini.read_bytes()
            for encoding in ("utf-8-sig", "gb18030", "utf-16"):
                try:
                    text = data.decode(encoding)
                    break
                except UnicodeError:
                    continue
            else:
                text = ""
            for match in re.finditer(r"^(?:DownloadPath|TempPath)\s*=\s*(.+?)\s*$", text, re.M | re.I):
                paths.append(Path(match.group(1).strip('"')))
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        for name in ("KuGou", "KugouMusic"):
            path = Path(f"{drive}:/{name}")
            if path.is_dir():
                paths.append(path)
    unique = sorted(set(p.resolve() for p in paths if p.is_dir()), key=lambda p: len(str(p)))
    return [p for p in unique if not any(parent != p and parent in p.parents for parent in unique)]


def track_from_path(path: Path, library: Library, explicit: bool = False) -> Track | None:
    is_hash = bool(HASH_NAME.fullmatch(path.name))
    if not explicit and path.suffix.lower() not in EXTENSIONS and not is_hash:
        return None
    size = path.stat().st_size
    if size < 16:
        return Track(path, path.name, "文件不完整", size) if explicit else None
    with path.open("rb") as f:
        head = f.read(4096)
    metadata = library.songs.get(path.stem.lower(), {})
    kind = ""
    if head[:16] in (KGM_MAGIC, VPR_MAGIC) and len(head) >= 24:
        version = struct.unpack_from("<I", head, 20)[0]
        kind = "KGG / v5" if version == 5 else f"KGM / v{version}"
    elif sniff(head):
        kind = sniff(head).upper()
    elif is_hash or path.suffix.lower() in EXTENSIONS or explicit:
        kind = "缓存待验证" if is_hash else "文件待验证"
    if not kind:
        return None
    song = str(metadata.get("SongName") or "")
    artist = str(metadata.get("Artist") or "")
    display = f"{artist} - {song}" if artist and song else song
    if not display:
        display = str(metadata.get("FileNamePure") or "")
        if display:
            display = Path(display).stem
    return Track(path, display or path.stem, kind, size, metadata)


def scan_paths(paths: list[Path], library: Library, cancelled: Callable[[], bool] = lambda: False, progress: Callable[[int], None] = lambda n: None, excluded: Path | None = None) -> tuple[list[Track], list[str]]:
    tracks, errors, seen = [], [], set()
    skip_dirs = {".git", ".venv", "node_modules", "$RECYCLE.BIN", "System Volume Information", "_internal", ".car-music-work"}

    def add(path: Path, explicit: bool = False):
        if cancelled():
            return
        try:
            resolved = path.resolve()
            if resolved in seen or (excluded and (resolved == excluded or excluded in resolved.parents)):
                return
            seen.add(resolved)
            track = track_from_path(resolved, library, explicit)
            if track:
                tracks.append(track)
                if len(tracks) % 20 == 0:
                    progress(len(tracks))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}：{exc}")

    for path in paths:
        if cancelled():
            break
        if path.is_file():
            add(path, True)
        elif path.is_dir():
            for directory, subdirs, files in os.walk(path, followlinks=False, onerror=lambda e: errors.append(str(e))):
                subdirs[:] = [n for n in subdirs if n not in skip_dirs and not (excluded and Path(directory, n).resolve() == excluded)]
                if cancelled():
                    break
                for filename in files:
                    if cancelled():
                        break
                    add(Path(directory, filename))
    return tracks, errors
