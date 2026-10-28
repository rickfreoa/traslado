# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Small, validated settings for the locally managed installation.

No environment-variable config discovery. Private models are derived from the
release directory; current.json may record the external OCR executable.
"""
from __future__ import annotations

import json
import os
from .pathguard import unsafe_link
from pathlib import Path
import re
import sys
import shutil

DEFAULT_LANGUAGES = ("por", "eng", "spa", "ita", "fra", "deu")
MAX_CONFIG_BYTES = 16 * 1024


def managed_root() -> Path | None:
    if sys.prefix == sys.base_prefix:
        return None
    return Path(sys.prefix).resolve().parent


def _is_link(path: Path) -> bool:
    return unsafe_link(path)


def runtime_settings() -> dict:
    settings = {"languages": list(DEFAULT_LANGUAGES),
                "data_root": str(Path.home() / "Documents" / "FreoaMarkdownConverter"),
                "config_path": None, "config_status": "unmanaged", "warnings": []}
    root = managed_root()
    if root is None:
        return settings
    path = root / "freoa-runtime.json"
    settings.update(config_path=str(path), config_status="absent")
    try:
        if not path.exists() and not _is_link(path):
            return settings
        if _is_link(path) or not path.is_file():
            raise ValueError("configuration must be a regular file")
        with path.open("rb") as reader:
            payload = reader.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise ValueError("configuration exceeds 16 KiB")
        config = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(config, dict) or type(config.get("schema_version")) is not int or config["schema_version"] != 1:
            raise ValueError("configuration schema invalid")
        languages = config.get("languages")
        if (not isinstance(languages, list) or not 1 <= len(languages) <= len(DEFAULT_LANGUAGES)
                or any(not isinstance(code, str) or not re.fullmatch(r"[a-z]{3}", code)
                       or code not in DEFAULT_LANGUAGES for code in languages)
                or len(set(languages)) != len(languages)):
            raise ValueError("configuration languages invalid")
        data_root = config.get("data_root")
        if not isinstance(data_root, str) or "\x00" in data_root or not Path(data_root).is_absolute():
            raise ValueError("configuration data_root invalid")
        settings.update(languages=languages, data_root=data_root, config_status="valid")
    except (OSError, ValueError, TypeError, RecursionError):
        settings.update(config_status="invalid")
        settings["warnings"].append("Configuração local inválida; usando idiomas e pasta de saída padrão.")
    return settings


def default_language() -> str:
    return "+".join(runtime_settings()["languages"])


def default_output_path() -> Path:
    return Path(runtime_settings()["data_root"]) / "resultados"


def managed_tesseract() -> str | None:
    root = managed_root()
    if root is None:
        return None
    folder = root / "ocr"
    executable = folder / "tesseract.exe"
    # In the release layout root is <managed>/releases/<id>.
    try:
        current_path = root.parent.parent / "current.json"
        current = json.loads(current_path.read_text(encoding="utf-8-sig"))
        configured = current.get("ocr", {}).get("tesseract") if current.get("release_id") == root.name else None
        if isinstance(configured, str) and Path(configured).is_absolute() and Path(configured).is_file():
            return configured
    except (OSError, ValueError, AttributeError):
        pass
    # Compatibility with the previous managed layout without current.json.
    if not _is_link(folder) and not _is_link(executable) and executable.is_file():
        return str(executable)
    return shutil.which("tesseract") if (folder / "tessdata").is_dir() else None



def managed_tessdata_for(executable: str, explicit: str | None = None) -> str | None:
    if explicit is not None:
        data = Path(explicit)
        if not data.is_absolute() or not data.is_dir():
            raise ValueError("Diretório tessdata inválido")
        return str(data)
    managed = managed_tesseract()
    if managed is None or os.path.normcase(os.path.abspath(executable)) != os.path.normcase(os.path.abspath(managed)):
        return None
    root = managed_root()
    data = root / "ocr/tessdata" if root else Path(managed).parent / "tessdata"
    if _is_link(data) or not data.is_dir():
        raise ValueError("Diretório tessdata do OCR local ausente ou inválido; reinstale em pasta nova")
    return str(data)
