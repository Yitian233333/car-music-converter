"""Offline Kugou codecs. Algorithm sources and licenses: THIRD_PARTY.md.

The client database is decrypted in memory only. No account or network API is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from Crypto.Cipher import AES

KGM_MAGIC = bytes.fromhex("7cd532eb86027f4ba8afa68e0fff9914")
VPR_MAGIC = bytes.fromhex("0528bc96e9e45a4391aabdd07af53631")
SQLITE_MAGIC = b"SQLite format 3\0"
DB_MASTER = bytes.fromhex("1d613145b247bf7f3d189672144fe4bf")
MASK32 = 0xFFFFFFFF


class AudioError(Exception):
    """Actionable failure, safe to display without exposing keys."""


class Cancelled(AudioError):
    pass


def sniff(data: bytes) -> str:
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"ID3"):
        return "mp3"
    if data.startswith(b"OggS"):
        return "ogg"
    if data[:4] in (b"RIFF", b"RF64") and data[8:12] == b"WAVE":
        return "wav"
    if data[4:8] == b"ftyp":
        return "m4a"
    if len(data) >= 2 and data[0] == 0xFF:
        if data[1] & 0xF6 == 0xF0:
            return "aac"
        if data[1] & 0xE0 == 0xE0 and data[1] & 0x06:
            return "mp3"
    if data.startswith(bytes.fromhex("3026b2758e66cf11")):
        return "wma"
    return ""


def db_page_key(page: int) -> bytes:
    return hashlib.md5(DB_MASTER + struct.pack("<I", page) + b"sAlT").digest()


def db_page_iv(page: int) -> bytes:
    seed = page + 1
    buf = bytearray()
    for _ in range(4):
        seed = (seed * 0x9EF4 - (seed // 0xCE26) * 0x7FFFFF07) & MASK32
        if seed & 0x80000000:
            seed = (seed + 0x7FFFFF07) & MASK32
        buf.extend(struct.pack("<I", seed))
    return hashlib.md5(buf).digest()


def decrypt_database(raw: bytes) -> bytes:
    if raw.startswith(SQLITE_MAGIC):
        return raw
    if len(raw) < 1024 or len(raw) % 1024:
        raise AudioError("密钥数据库不完整或版本不受支持，请选择 KGMusicV3.db。")
    page = bytearray(raw[:1024])
    expected = bytes(page[16:24])
    page[16:24] = page[8:16]
    page[16:] = AES.new(db_page_key(1), AES.MODE_CBC, db_page_iv(1)).decrypt(page[16:])
    if page[16:24] != expected or page[16:18] != b"\x04\x00":
        raise AudioError("无法读取此版本的酷狗数据库；请检查文件，或更新本工具的数据库解析器。")
    page[:16] = SQLITE_MAGIC
    out = bytearray(page)
    for offset in range(1024, len(raw), 1024):
        page_no = offset // 1024 + 1
        out.extend(AES.new(db_page_key(page_no), AES.MODE_CBC, db_page_iv(page_no)).decrypt(raw[offset:offset + 1024]))
    return bytes(out)


@dataclass
class Library:
    keys: dict[str, str] = field(default_factory=dict)
    songs: dict[str, dict] = field(default_factory=dict)
    databases: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def load(self, path: Path) -> None:
        # SQLite deserialize avoids leaving a plaintext copy of the key database.
        plain = decrypt_database(path.read_bytes())
        conn = sqlite3.connect(":memory:")
        try:
            conn.deserialize(plain)
            conn.execute("PRAGMA query_only=ON")
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM ShareFileItems")
            for row in rows:
                original = dict(row)
                lower = {k.lower(): v for k, v in original.items()}
                names = ("EncryptionKeyId", "EncryptionKey", "SongName", "Artist", "Album", "FileName", "FileNamePure", "MD5", "OriginalHash", "FileHash", "Hash", "BitRate", "Duration")
                item = {k: lower.get(k.lower()) for k in names}
                key_id = str(item.get("EncryptionKeyId") or "").strip().strip("\0")
                ekey = str(item.get("EncryptionKey") or "").strip()
                if key_id and ekey:
                    self.keys[key_id.lower()] = ekey
                # Retain display metadata, never the encryption key in song records.
                safe = {k: item.get(k) for k in ("SongName", "Artist", "Album", "FileName", "FileNamePure", "FileHash", "Hash", "MD5", "OriginalHash", "BitRate", "Duration")}
                for value in (key_id, item.get("FileHash"), item.get("Hash"), item.get("MD5"), item.get("OriginalHash")):
                    if value:
                        self.songs[str(value).lower()] = safe
            self.databases.append(str(path))
        except sqlite3.Error as exc:
            raise AudioError("数据库结构无法读取，请先关闭酷狗后重试，或选择另一份 KGMusicV3.db。") from exc
        finally:
            conn.close()


def tea_block(block: bytes, key: bytes) -> bytes:
    v0, v1 = struct.unpack(">2I", block)
    k0, k1, k2, k3 = struct.unpack(">4I", key)
    total = (0x9E3779B9 * 16) & MASK32
    for _ in range(16):
        v1 = (v1 - (((v0 << 4) + k2) ^ (v0 + total) ^ ((v0 >> 5) + k3))) & MASK32
        v0 = (v0 - (((v1 << 4) + k0) ^ (v1 + total) ^ ((v1 >> 5) + k1))) & MASK32
        total = (total - 0x9E3779B9) & MASK32
    return struct.pack(">2I", v0, v1)


def tencent_tea(data: bytes, key: bytes) -> bytes:
    if len(data) < 16 or len(data) % 8 or len(key) != 16:
        raise AudioError("歌曲密钥格式不受支持。")
    previous_cipher = bytes(8)
    previous_mixed = bytes(8)
    plain = bytearray()
    for at in range(0, len(data), 8):
        current = data[at:at + 8]
        mixed = tea_block(bytes(a ^ b for a, b in zip(current, previous_mixed)), key)
        plain.extend(a ^ b for a, b in zip(mixed, previous_cipher))
        previous_mixed, previous_cipher = mixed, current
    pad = plain[0] & 7
    if len(plain) < 1 + pad + 2 + 7 or plain[-7:] != bytes(7):
        raise AudioError("歌曲密钥校验失败，请在酷狗客户端中重新播放或下载该歌曲后重试。")
    return bytes(plain[1 + pad + 2:-7])


def derive_ekey(ekey: str | bytes) -> bytes:
    try:
        raw = base64.b64decode(ekey, validate=True)
        prefix = b"QQMusic EncV2,Key:"
        if raw.startswith(prefix):
            raw = tencent_tea(raw[len(prefix):], b"386ZJY!@#*$%^&)(")
            raw = tencent_tea(raw, b"**#!(#$%&^a1cZ,T")
            raw = base64.b64decode(raw, validate=True)
        if len(raw) < 16:
            raise AudioError("歌曲密钥长度不足。")
        simple = bytes.fromhex("695646382b20150b")
        tea_key = bytes(value for pair in zip(simple, raw[:8]) for value in pair)
        key = raw[:8] + tencent_tea(raw[8:], tea_key)
        if not key or len(key) > 65536:
            raise AudioError("歌曲密钥长度不受支持。")
        return key
    except (ValueError, TypeError) as exc:
        raise AudioError("歌曲密钥格式不受支持，请在酷狗客户端重新播放或下载后重试。") from exc


class QmcMap:
    def __init__(self, key: bytes):
        self.key = np.frombuffer(key, dtype=np.uint8)

    def decrypt(self, data: bytes, offset: int) -> bytes:
        pos = np.arange(offset, offset + len(data), dtype=np.uint64)
        pos = np.where(pos > 0x7FFF, pos % 0x7FFF, pos)
        idx = (pos * pos + 71214) % len(self.key)
        values = self.key[idx].astype(np.uint16)
        shift = ((idx & 7) + 4) % 8
        mask = ((values << shift) | (values >> shift)).astype(np.uint8)
        return (np.frombuffer(data, dtype=np.uint8) ^ mask).tobytes()


class QmcRC4:
    def __init__(self, key: bytes):
        self.key, self.n = key, len(key)
        box = [i & 255 for i in range(self.n)]
        j = 0
        for i in range(self.n):
            j = (j + box[i] + key[i]) % self.n
            box[i], box[j] = box[j], box[i]
        self.box = box
        self.hash = 1
        for value in key:
            if not value:
                continue
            nxt = self.hash * value & MASK32
            if nxt <= self.hash:
                break
            self.hash = nxt

    def skip(self, index: int) -> int:
        seed = self.key[index % self.n]
        if not seed:
            # No finite skip exists for a zero seed. Keep access bounded;
            # strict audio validation will reject an incompatible stream.
            return 0
        return int(self.hash / ((index + 1) * seed) * 100.0) % self.n

    def decrypt(self, data: bytes, offset: int) -> bytes:
        out = bytearray(data)
        at = 0
        while at < len(out) and offset + at < 128:
            out[at] ^= self.key[self.skip(offset + at)]
            at += 1
        while at < len(out):
            stream_offset = offset + at
            length = min(len(out) - at, 5120 - stream_offset % 5120)
            skip = stream_offset % 5120 + self.skip(stream_offset // 5120)
            box, j, k = self.box.copy(), 0, 0
            for i in range(-skip, length):
                j = (j + 1) % self.n
                k = (box[j] + k) % self.n
                box[j], box[k] = box[k], box[j]
                if i >= 0:
                    out[at + i] ^= box[(box[j] + box[k]) % self.n]
            at += length
        return bytes(out)


def qmc_cipher(ekey: str):
    key = derive_ekey(ekey)
    return QmcRC4(key) if len(key) > 300 else QmcMap(key)


def kugou_md5(value: bytes) -> bytes:
    digest = hashlib.md5(value).digest()
    return b"".join(digest[i:i + 2] for i in range(14, -1, -2))


class KgmV3:
    def __init__(self, header: bytes):
        if struct.unpack_from("<I", header, 24)[0] != 1:
            raise AudioError("此 KGM 文件使用了尚未支持的加密槽位。")
        self.slot = np.frombuffer(kugou_md5(bytes.fromhex("6c2c2f27")), dtype=np.uint8)
        self.file = np.frombuffer(kugou_md5(header[44:60]) + b"\x6b", dtype=np.uint8)

    def decrypt(self, data: bytes, offset: int) -> bytes:
        pos = np.arange(offset, offset + len(data), dtype=np.uint64)
        b = np.frombuffer(data, dtype=np.uint8) ^ self.file[pos % 17]
        b ^= b << 4
        b ^= self.slot[pos % 16]
        collapse = (pos ^ (pos >> 8) ^ (pos >> 16) ^ (pos >> 24)).astype(np.uint8)
        return (b ^ collapse).tobytes()


class KgmV2:
    def __init__(self, header: bytes):
        tables = json.loads(Path(__file__).with_name("kgm_tables.json").read_text())
        self.t1 = np.array(tables["table1"], dtype=np.uint8)
        self.t2 = np.array(tables["table2"], dtype=np.uint8)
        self.tv = np.array(tables["tableV2"], dtype=np.uint8)
        self.vpr = np.array(tables["vprKey"], dtype=np.uint8) if header.startswith(VPR_MAGIC) else None
        self.key = np.frombuffer(header[28:44] + b"\0", dtype=np.uint8)

    def decrypt(self, data: bytes, offset: int) -> bytes:
        pos = np.arange(offset, offset + len(data), dtype=np.uint64)
        shifted = pos >> 4
        mask = self.tv[pos % 272].copy()
        while np.any(shifted >= 17):
            active = shifted >= 17
            mask[active] ^= self.t1[shifted[active] % 272]
            shifted[active] >>= 4
            mask[active] ^= self.t2[shifted[active] % 272]
            shifted[active] >>= 4
        b = np.frombuffer(data, dtype=np.uint8) ^ self.key[pos % 17] ^ mask
        b ^= b << 4
        if self.vpr is not None:
            b ^= self.vpr[pos % 17]
        return b.tobytes()


def parse_header(head: bytes, file_size: int) -> tuple[int, int, str]:
    if len(head) < 60 or head[:16] not in (KGM_MAGIC, VPR_MAGIC):
        raise AudioError("文件不是完整的 KGM／KGMA／KGG 音频，或缓存尚未下载完成。")
    offset, version = struct.unpack_from("<2I", head, 16)
    if offset < 60 or offset >= file_size or offset > 16 * 1024 * 1024:
        raise AudioError("音频文件头中的数据偏移无效，缓存可能不完整。")
    audio_hash = ""
    if version == 5:
        if len(head) < 72:
            raise AudioError("KGG 文件头不完整。")
        length = struct.unpack_from("<I", head, 68)[0]
        if not 1 <= length <= 1024 or 72 + length > min(len(head), offset):
            raise AudioError("KGG 文件中的歌曲标识无效。")
        audio_hash = head[72:72 + length].decode("ascii", errors="strict").strip("\0").lower()
    return offset, version, audio_hash


def decoder_for(head: bytes, size: int, library: Library, cache_hash: str = ""):
    if sniff(head):
        return 0, None, "原始音频"
    if head[:16] in (KGM_MAGIC, VPR_MAGIC):
        offset, version, audio_hash = parse_header(head, size)
        if version == 3:
            return offset, KgmV3(head), "KGM v3"
        if version in (2, 4):
            return offset, KgmV2(head), f"KGM v{version}"
        if version == 5:
            ekey = library.keys.get(audio_hash)
            if not ekey:
                raise AudioError("本机数据库没有这首歌的密钥。请在酷狗中完整播放或重新下载，再点“重新扫描密钥库”。")
            return offset, qmc_cipher(ekey), "KGG / KGM v5"
        raise AudioError(f"尚未支持 KGM 加密版本 {version}，请保留原文件。")
    if cache_hash and cache_hash.lower() in library.keys:
        return 0, qmc_cipher(library.keys[cache_hash.lower()]), "酷狗无扩展名缓存"
    raise AudioError("无法识别文件头，或该缓存缺少本机歌曲密钥；不能仅通过更改后缀转换。")
