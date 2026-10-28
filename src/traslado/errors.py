# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Stable conversion failures, independent of user-facing messages."""
from __future__ import annotations

import subprocess
import zipfile
import errno
import zlib

CODES = frozenset({"unsupported", "needs_ocr", "malformed", "encrypted",
                   "resource_limit", "missing_part", "io", "timeout", "cancelled", "internal"})


class ConversionError(ValueError, RuntimeError):
    def __init__(self, code: str, message: str, data: dict | None = None):
        if code not in CODES:
            raise ValueError("Código de conversão inválido: " + code)
        super().__init__(message)
        self.code = code
        self.data = data


class ConversionTimeout(ConversionError, TimeoutError):
    def __init__(self, message: str):
        super().__init__("timeout", message)


def exception_name(exc: Exception) -> str:
    # Optional-image/table warnings used the original built-in exception name.
    return getattr(exc, "_warning_name", "ValueError") if isinstance(exc, ConversionError) else type(exc).__name__


def classify(exc: Exception) -> ConversionError:
    if isinstance(exc, ConversionError):
        return exc
    if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
        code = "timeout"
    elif isinstance(exc, MemoryError):
        code = "resource_limit"
    elif isinstance(exc, (zipfile.BadZipFile, zlib.error)):
        code = "malformed"
    elif isinstance(exc, UnicodeError):
        code = "malformed"
    elif type(exc).__name__ in {"DecompressionBombError", "DecompressionBombWarning"}:
        code = "resource_limit"
    elif isinstance(exc, ValueError) and str(exc) in {
            "Tabela renderizada excede limite de células", "Quantidade de chunks excede limite",
            "Chunks excedem limite de saída"}:
        # These producers are outside E3's editable surface; retain their text.
        code = "resource_limit"
    elif isinstance(exc, ValueError) and str(exc) == "Informe caminho absoluto de mineru-kit instalado separadamente":
        code = "missing_part"
    elif isinstance(exc, OSError):
        code = "resource_limit" if (exc.errno in {errno.ENOMEM, errno.EAGAIN}
                                    or getattr(exc, "winerror", None) in {8, 14, 1450, 1816}) else "io"
    else:
        code = "internal"
    return ConversionError(code, str(exc))


def localized(document, page, exc: Exception) -> None:
    """Keep localized errors off the serialized document schema."""
    issues = getattr(document, "_conversion_issues", [])
    issue = classify(exc)
    if issue.code == "missing_part" and not any(b.kind == "text" and b.text.strip() for b in page.blocks):
        issue = ConversionError("needs_ocr", str(exc))
    issues.append((page.number, issue))
    document._conversion_issues = issues


def partial_error(document) -> ConversionError | None:
    issues = sorted(getattr(document, "_conversion_issues", []), key=lambda item: item[0])
    if issues:
        first = issues[0][1]
        pages = [number for number, issue in issues if issue.code == "needs_ocr"]
        return ConversionError(first.code, str(first), {"error_pages": sorted(set(pages))} if first.code == "needs_ocr" else first.data)
    return None
