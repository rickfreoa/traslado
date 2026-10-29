# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Bounded, stdlib-only content signatures. Plain text has no signature."""
from __future__ import annotations

from pathlib import Path
import re
import zipfile
import zlib

from .errors import ConversionError



def sniff(path: Path, *, legacy: bool = False) -> str | None:
    with Path(path).open("rb") as stream:
        head = stream.read(4096)
    text_extension = Path(path).suffix.lower() in {
        ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm"}
    pdf = re.match(br"%PDF-\d\.\d", head) if text_extension else re.search(br"%PDF-\d\.\d", head[:1032])
    if pdf and pdf.start() <= 1024:
        return ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    # SOI must be followed by a defined marker (not stuffed data or another SOI).
    if head.startswith(b"\xff\xd8\xff"):
        marker = head[3:].lstrip(b"\xff")[:1]
        if marker and marker[0] in {*range(0xc0, 0xd0), 0xda, 0xdb, 0xdc, 0xdd, 0xde, 0xdf, *range(0xe0, 0xff)}:
            return ".jpg"
    if len(head) >= 8 and head[:4] in {b"II*\x00", b"MM\x00*"}:
        if int.from_bytes(head[4:8], "little" if head[:2] == b"II" else "big") >= 8:
            return ".tif"
    if (len(head) >= 26 and head[:2] == b"BM" and head[6:10] == bytes(4)
            and int.from_bytes(head[2:6], "little") >= 26
            and int.from_bytes(head[10:14], "little") >= 26
            and int.from_bytes(head[14:18], "little") in {12, 40, 52, 56, 64, 108, 124}):
        return ".bmp"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    # GIF is intentionally absent: the built-in registry does not support it.
    if head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return _ole_format(path, head) or "ole2"
    if head.startswith(b"{\\rtf"):
        return ".rtf"
    text = head.removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    if re.match(br"(?:<!doctype\s+html(?:\s|>)|<html(?:\s|>))", text):
        return ".html"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "mimetype" in names:
                    info = archive.getinfo("mimetype")
                    if info.file_size <= 256:
                        with archive.open(info) as stream:
                            mime = stream.read(257)
                        if len(mime) > 256:
                            return None
                        mime = mime.strip()
                        if mime == b"application/epub+zip":
                            return ".epub"
                        odf = {b"text": ".odt", b"spreadsheet": ".ods", b"presentation": ".odp"}
                        prefix = b"application/vnd.oasis.opendocument."
                        if mime.startswith(prefix):
                            return odf.get(mime[len(prefix):], "odf")
                for part, suffix in (("word/document.xml", ".docx"), ("xl/workbook.xml", ".xlsx"),
                                     ("ppt/presentation.xml", ".pptx")):
                    if part in names:
                        return suffix
                if "[Content_Types].xml" in names:
                    info = archive.getinfo("[Content_Types].xml")
                    if info.file_size <= 65536:
                        with archive.open(info) as stream:
                            types = stream.read(65537)
                        if len(types) > 65536:
                            return None
                        for token, suffix in ((b"wordprocessingml.document.main+xml", ".docx"),
                                              (b"spreadsheetml.sheet.main+xml", ".xlsx"),
                                              (b"presentationml.presentation.main+xml", ".pptx")):
                            if token in types:
                                return suffix
        except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError):
            return None
    return None


def _ole_format(path: Path, head: bytes) -> str | None:
    """Bounded CFB directory walk; no optional parser in the supervisor."""
    import struct
    if len(head) < 512 or head[30:32] not in (b"\x09\x00", b"\x0c\x00"):
        return None
    size = 1 << int.from_bytes(head[30:32], "little")
    sector = int.from_bytes(head[48:52], "little")
    fat_ids = struct.unpack_from("<109I", head, 76)
    names = {"WordDocument": ".doc", "PowerPoint Document": ".ppt", "Workbook": ".xls", "Book": ".xls"}
    seen = set()
    with path.open("rb") as stream:
        for _ in range(4096):
            if sector >= 0xFFFFFFFA or sector in seen:
                return None
            seen.add(sector)
            stream.seek((sector + 1) * size)
            directory = stream.read(size)
            for offset in range(0, len(directory) - 127, 128):
                length = int.from_bytes(directory[offset+64:offset+66], "little")
                if 2 <= length <= 64 and length % 2 == 0:
                    name = directory[offset:offset+length-2].decode("utf-16-le", errors="replace")
                    if name in names:
                        return names[name]
            fat_index, entry = divmod(sector, size // 4)
            if fat_index >= len(fat_ids) or fat_ids[fat_index] >= 0xFFFFFFFA:
                return None
            stream.seek((fat_ids[fat_index] + 1) * size + entry * 4)
            value = stream.read(4)
            if len(value) != 4:
                return None
            sector = int.from_bytes(value, "little")
    raise ConversionError("resource_limit", "Diretório OLE excede limite de inspeção")
