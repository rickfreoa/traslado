# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import importlib.util
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from . import __version__
from .registry import REGISTRY
from .ocr import find_tesseract
from .runtime import managed_tessdata_for, runtime_settings


def inspect(tesseract: str | None = None, tessdata: str | None = None) -> dict:
    settings = runtime_settings()
    result = {"version": __version__, "python": sys.version.split()[0],
              "default_conversion_network": "no_network_io_in_builtin_adapters",
              "advanced_adapter_network": "third_party_process_without_os_network_sandbox",
              "capabilities": {}, "tesseract": None, "tessdata": None, "languages": [],
              "default_language": "+".join(settings["languages"]),
              "default_output": str(Path(settings["data_root"]) / "resultados"),
              "runtime_config": {"path": settings["config_path"], "status": settings["config_status"]},
              "warnings": list(settings["warnings"])}
    for module, distribution in (("fitz", "PyMuPDF"), ("PIL", "Pillow"), ("docx", "python-docx"),
                                 ("openpyxl", "openpyxl"), ("pptx", "python-pptx"), ("tkinter", None)):
        available = importlib.util.find_spec(module) is not None
        item = {"available": available}
        if available and distribution:
            item["version"] = importlib.metadata.version(distribution)
        result["capabilities"][module] = item
    try:
        exe = find_tesseract(tesseract)
        result["tesseract"] = exe
        if exe:
            argv = [exe]
            data = managed_tessdata_for(exe, tessdata)
            result["tessdata"] = data
            if data:
                argv += ["--tessdata-dir", data]
            proc = subprocess.run(argv + ["--list-langs"], capture_output=True, timeout=10,
                                  creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            if proc.returncode == 0:
                result["languages"] = [line.strip() for line in proc.stdout.decode("utf-8", errors="replace").splitlines()
                                       if line.strip() and not line.lower().startswith("list of available")]
            else:
                result["warnings"].append("Tesseract não conseguiu listar idiomas")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["warnings"].append(str(exc))
    result["license_notice"] = "PyMuPDF: AGPL ou licença comercial. MinerU não é distribuído. Consulte THIRD-PARTY.md."
    result["converters"] = []
    for converter in sorted(REGISTRY, key=lambda item: item.id):
        status = converter.status(tesseract)
        result["converters"].append({
            "id": converter.id, "extensions": list(converter.extensions),
            "state": status.state, "code": status.code, "detail": status.detail,
            "capabilities": list(converter.capabilities), "known_losses": list(converter.known_losses),
        })
    return result
