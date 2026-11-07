# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

from datetime import datetime
import importlib.util
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from . import __version__
from . import platform as plat
from .registry import REGISTRY
from .ocr import find_tesseract
from .revocation import read_revocation_state, _parse_utc, _ensure_utc, RevocationError
from .runtime import managed_tessdata_for, runtime_settings, managed_root


def inspect(tesseract: str | None = None, tessdata: str | None = None, *, root: Path | None = None, now: datetime | str | None = None) -> dict:
    settings = runtime_settings()
    result = {"version": __version__, "module": "traslado", "python": sys.version.split()[0],
              "default_conversion_network": "no_network_io_in_builtin_adapters",
              "advanced_adapter_network": "third_party_process_without_os_network_sandbox",
              "capabilities": {}, "tesseract": None, "tessdata": None, "languages": [],
              "default_language": "+".join(settings["languages"]),
              "default_output": str(Path(settings["data_root"]) / "resultados"),
              "runtime_config": {"path": settings["config_path"], "status": settings["config_status"]},
              "warnings": list(settings["warnings"])}
    for module, distribution in (("fitz", "PyMuPDF"), ("PIL", "Pillow"), ("docx", "python-docx"),
                                 ("openpyxl", "openpyxl"), ("pptx", "python-pptx"), ("anydoc", "firecrawl-anydoc"), ("tkinter", None)):
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
    install_root = root
    if install_root is None:
        m_root = managed_root()
        if m_root is not None:
            candidate = m_root.parent.parent
            if (candidate / "state" / "revocation.json").is_file():
                install_root = candidate
        if install_root is None:
            p_root = plat.managed_root()
            if (p_root / "state" / "revocation.json").is_file():
                install_root = p_root
            elif m_root is not None:
                install_root = m_root.parent.parent
            else:
                install_root = p_root

    rev_state = None
    if install_root is not None:
        try:
            rev_state = read_revocation_state(install_root)
        except RevocationError as exc:
            result["warnings"].append(f"Canal de revogação: falha ao ler estado local ({exc})")
            rev_state = None

    now_dt = _ensure_utc(now)
    if rev_state is not None:
        try:
            expires_at = _parse_utc(rev_state["expires_at"])
            delta_days = (expires_at - now_dt).total_seconds() / 86400
            days_remaining = round(delta_days, 1)
            result["revocation"] = {
                "sequence": rev_state["sequence"],
                "expires_at": rev_state["expires_at"],
                "days_remaining": days_remaining,
                "dias_restantes": days_remaining,
            }
            if delta_days < 7.0:
                result["warnings"].append(
                    f"Canal de revogação: restam menos de 7 dias de validade ({days_remaining:.1f} dias restantes, expira em {rev_state['expires_at']})"
                )
        except Exception:
            result["revocation"] = {
                "sequence": rev_state.get("sequence"),
                "expires_at": rev_state.get("expires_at"),
                "days_remaining": None,
                "dias_restantes": None,
            }
            result["warnings"].append("Canal de revogação: estado local contém datas inválidas")
    else:
        result["revocation"] = None
        result["warnings"].append("Canal de revogação: nenhum documento de revogação aceito pela instalação")

    return result
