# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Gates da candidata: doctor, conversão de fixture embutida e digests do bootstrap."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import tempfile
from typing import Callable

from .. import runner
from ..model import Options
from ._common import normalize_languages

# Raiz do pacote `traslado`: as fixtures empacotadas ficam em traslado/assets.
_PACOTE = Path(__file__).resolve().parent.parent

def evaluate_doctor_gate(
    report: dict,
    *,
    profile: str = "Full",
    languages: list[str] | None = None,
    ocr_installed: bool = False,
) -> bool:
    if not isinstance(report, dict):
        return False
    caps = report.get("capabilities")
    if not isinstance(caps, dict):
        return False
    if profile == "Full":
        for mod in ("fitz", "PIL", "docx", "openpyxl", "pptx"):
            if not isinstance(caps.get(mod), dict) or caps[mod].get("available") is not True:
                return False
    if ocr_installed:
        langs = normalize_languages(languages)
        avail = set(report.get("languages") or [])
        if not set(langs) <= avail:
            return False
    return True


def run_fixture_gate(
    *,
    profile: str = "Full",
    tesseract: str | None = None,
    tessdata: str | None = None,
    execute_fn: Callable[..., dict] | None = None,
    chunks: bool = False,
) -> bool:
    assets_dir = _PACOTE / "assets"
    docx_fixture = assets_dir / "fixture.docx"
    pdf_fixture = assets_dir / "fixture.pdf"
    exec_call = execute_fn or runner.execute

    with tempfile.TemporaryDirectory(prefix="traslado-fixture-") as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        targets: list[Path] = []
        if profile == "Full":
            if not docx_fixture.is_file() or not pdf_fixture.is_file():
                print("FIXTURE_GATE_FAILED: fixture DOCX/PDF ausente no pacote instalado", file=sys.stderr)
                return False
            targets = [docx_fixture, pdf_fixture]
        else:
            if docx_fixture.is_file() and importlib.util.find_spec("docx") is not None:
                targets = [docx_fixture]
            else:
                txt_fixture = tmp_path / "fixture.txt"
                txt_fixture.write_text("Traslado verificacao de instalacao Core OK.\n", encoding="utf-8")
                targets = [txt_fixture]

        opts = Options(mode="native", chunks=chunks, tesseract=tesseract, tessdata=tessdata)
        for fixture in targets:
            try:
                res = exec_call(fixture, out_dir, opts)
            except Exception as exc:
                print(f"FIXTURE_GATE_FAILED {fixture.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                return False
            if (not isinstance(res, dict) or res.get("status") not in {"complete", "partial"}
                    or "error_code" in res or not res.get("output")):
                resumo = {k: res.get(k) for k in ("status", "error_code", "error", "warning_summary")} if isinstance(res, dict) else res
                print(f"FIXTURE_GATE_FAILED {fixture.name}: {resumo!r}", file=sys.stderr)
                return False
            out_path = Path(res["output"])
            required = ["document.md", "document.json", "manifest.json", "LEIA-AVISOS.txt"]
            if chunks:
                required.append("chunks/chunks.jsonl")
            ausentes = [name for name in required if not (out_path / name).is_file()]
            if ausentes:
                print(f"FIXTURE_GATE_FAILED {fixture.name}: arquivos ausentes {ausentes}", file=sys.stderr)
                return False
    return True


def validate_bootstrap_digests(digests: dict | None, version: str, profile: str, extras: list[str]) -> dict:
    required = {f"traslado-{version}-py3-none-any.whl", f"requirements-{profile.lower()}.hashes.lock"}
    if "legacy" in extras:
        required.add("requirements-legacy.hashes.lock")
    if not isinstance(digests, dict) or not required <= digests.keys():
        raise ValueError("Mapa de digests vazio ou incompleto do bootstrap")
    if any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v)
           for k, v in digests.items()):
        raise ValueError("Mapa de digests invalido do bootstrap")
    return digests
