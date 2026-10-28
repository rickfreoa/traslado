# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Bounded, stdlib-only content signatures. Plain text has no signature."""
from __future__ import annotations

from pathlib import Path
import re
import zipfile

from .errors import ConversionError


def sniff(path: Path) -> str | None:
    with Path(path).open("rb") as stream:
        head = stream.read(4096)
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    for signature, suffix in ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
                              (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"BM", ".bmp")):
        if head.startswith(signature):
            return suffix
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    # GIF is intentionally absent: the built-in registry does not support it.
    if head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return "ole2"
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
                        mime = archive.read(info).strip()
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
                        types = archive.read(info)
                        for token, suffix in ((b"wordprocessingml.document.main+xml", ".docx"),
                                              (b"spreadsheetml.sheet.main+xml", ".xlsx"),
                                              (b"presentationml.presentation.main+xml", ".pptx")):
                            if token in types:
                                return suffix
        except zipfile.BadZipFile as exc:
            raise ConversionError("malformed", str(exc)) from exc
    return None
