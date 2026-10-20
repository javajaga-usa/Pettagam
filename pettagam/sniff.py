"""Identify a file from its first bytes: real type, image size, camera data, audio tags.

Stdlib only, so it runs the same on macOS and Ubuntu. Reads at most HEAD_BYTES per file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

HEAD_BYTES = 64 * 1024  # JPEG EXIF (APP1) is at most 64 KB


@dataclass
class Sniff:
    kind: Optional[str] = None      # canonical extension of the detected format, e.g. "jpg"
    width: Optional[int] = None
    height: Optional[int] = None
    camera: Optional[str] = None    # "Make Model" from EXIF, when present
    has_exif: bool = False
    has_tags: bool = False          # audio has artist/title tags


def sniff_file(path: str) -> Sniff:
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
    except OSError:
        return Sniff()
    return sniff_bytes(head)


def sniff_bytes(b: bytes) -> Sniff:
    if b.startswith(b"\xff\xd8\xff"):
        return _jpeg(b)
    if b.startswith(b"\x89PNG\r\n\x1a\n") and len(b) >= 24:
        w, h = struct.unpack(">II", b[16:24])
        return Sniff("png", w, h, has_exif=b"eXIf" in b[:4096])
    if b[:6] in (b"GIF87a", b"GIF89a") and len(b) >= 10:
        w, h = struct.unpack("<HH", b[6:10])
        return Sniff("gif", w, h)
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return _webp(b)
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return Sniff("wav")
    if b[:4] == b"RIFF" and b[8:12] == b"AVI ":
        return Sniff("avi")
    if b[:2] == b"BM" and len(b) >= 26:
        w, h = struct.unpack("<ii", b[18:26])
        return Sniff("bmp", w, abs(h))
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        s = Sniff("tiff")
        _tiff_exif(b, 0, s)
        return s
    if b[4:8] == b"ftyp":
        return _ftyp(b)
    if b.startswith(b"%PDF"):
        return Sniff("pdf")
    if b.startswith(b"PK\x03\x04"):
        return Sniff(_zip_kind(b))
    if b.startswith(b"\x1a\x45\xdf\xa3"):
        return Sniff("webm" if b"webm" in b[:64] else "mkv")
    if b.startswith(b"ID3"):
        return Sniff("mp3", has_tags=_id3_has_tags(b))
    if len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0:
        return Sniff("mp3")
    if b.startswith(b"fLaC"):
        return Sniff("flac", has_tags=b"ARTIST=" in b.upper() or b"TITLE=" in b.upper())
    if b.startswith(b"OggS"):
        return Sniff("ogg", has_tags=b"ARTIST=" in b.upper())
    if b.startswith(b"7z\xbc\xaf\x27\x1c"):
        return Sniff("7z")
    if b.startswith(b"Rar!\x1a\x07"):
        return Sniff("rar")
    if b.startswith(b"\x1f\x8b"):
        return Sniff("gz")
    if len(b) > 262 and b[257:262] == b"ustar":
        return Sniff("tar")
    if b.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return Sniff("ole")  # legacy Office (doc/xls/ppt); extension decides which
    head = b[:1024].lstrip().lower()
    if head.startswith(b"<?xml") and b"<svg" in b[:4096].lower() or head.startswith(b"<svg"):
        return Sniff("svg")
    return Sniff()


def _jpeg(b: bytes) -> Sniff:
    s = Sniff("jpg")
    i, n = 2, len(b)
    while i + 4 <= n:
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", b[i + 2:i + 4])[0]
        if marker == 0xE1 and b[i + 4:i + 10] == b"Exif\x00\x00":
            s.has_exif = True
            _tiff_exif(b, i + 10, s)
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3) and i + 9 <= n:
            s.height, s.width = struct.unpack(">HH", b[i + 5:i + 9])
            break
        elif marker == 0xDA:  # start of scan: no more headers
            break
        i += 2 + seg_len
    return s


def _tiff_exif(b: bytes, base: int, s: Sniff) -> None:
    """Read Make (0x010F) and Model (0x0110) from IFD0 of a TIFF block starting at base."""
    try:
        order = b[base:base + 2]
        e = "<" if order == b"II" else ">"
        ifd = struct.unpack(e + "I", b[base + 4:base + 8])[0]
        count = struct.unpack(e + "H", b[base + ifd:base + ifd + 2])[0]
        found = {}
        for k in range(min(count, 64)):
            off = base + ifd + 2 + 12 * k
            tag, typ, cnt, val = struct.unpack(e + "HHII", b[off:off + 12])
            if tag in (0x010F, 0x0110) and typ == 2:
                start = base + val if cnt > 4 else off + 8
                found[tag] = b[start:start + cnt].split(b"\x00")[0].decode("ascii", "replace").strip()
        cam = " ".join(v for v in (found.get(0x010F), found.get(0x0110)) if v)
        if cam:
            s.camera = cam
            s.has_exif = True
    except (struct.error, IndexError):
        pass


def _webp(b: bytes) -> Sniff:
    s = Sniff("webp")
    chunk = b[12:16]
    try:
        if chunk == b"VP8X":
            s.width = 1 + int.from_bytes(b[24:27], "little")
            s.height = 1 + int.from_bytes(b[27:30], "little")
            s.has_exif = bool(b[20] & 0x08)
        elif chunk == b"VP8 ":
            w, h = struct.unpack("<HH", b[26:30])
            s.width, s.height = w & 0x3FFF, h & 0x3FFF
        elif chunk == b"VP8L":
            bits = int.from_bytes(b[21:25], "little")
            s.width, s.height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except (struct.error, IndexError):
        pass
    return s


_FTYP = {
    b"heic": "heic", b"heix": "heic", b"hevc": "heic", b"mif1": "heic", b"msf1": "heic",
    b"avif": "avif", b"qt  ": "mov", b"M4A ": "m4a", b"M4B ": "m4a", b"M4V ": "m4v",
    b"crx ": "cr3", b"3gp4": "3gp", b"3gp5": "3gp", b"3g2a": "3gp",
}


def _ftyp(b: bytes) -> Sniff:
    brand = b[8:12]
    return Sniff(_FTYP.get(brand, "mp4"))


def _zip_kind(b: bytes) -> str:
    if b"word/" in b:
        return "docx"
    if b"xl/" in b:
        return "xlsx"
    if b"ppt/" in b:
        return "pptx"
    if b"mimetypeapplication/vnd.oasis.opendocument.text" in b:
        return "odt"
    if b"mimetypeapplication/epub+zip" in b:
        return "epub"
    return "zip"


def _id3_has_tags(b: bytes) -> bool:
    return any(t in b[:4096] for t in (b"TIT2", b"TPE1", b"TALB"))
