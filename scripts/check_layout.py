# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Verifica que o layout do PyMuPDF carrega neste ambiente, sem rede e com os pesos inventariados (ADR 0001)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import onnxruntime
import pymupdf
import pymupdf.layout  # ativa o analisador

INVENTARIO = Path(__file__).resolve().parents[1] / "docs" / "adr" / "0001-pesos.json"


def fixture() -> pymupdf.Page:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Capitulo I", fontsize=18, fontname="tibo")
    page.insert_textbox(pymupdf.Rect(72, 110, 285, 700), "Texto de corpo. " * 120, fontsize=10, fontname="tiro")
    page.insert_textbox(pymupdf.Rect(310, 110, 523, 700), "Texto de corpo. " * 120, fontsize=10, fontname="tiro")
    return page


def main() -> int:
    inventario = {p["arquivo"]: p for p in json.loads(INVENTARIO.read_text(encoding="utf-8"))["pesos_onnx"]}
    raiz = Path(pymupdf.__file__).parent / "layout" / "resources" / "onnx"
    divergentes = [nome for nome, peso in inventario.items()
                   if hashlib.sha256((raiz / nome).read_bytes()).hexdigest() != peso["sha256"]]
    if divergentes:
        print("pesos divergentes:", divergentes, file=sys.stderr)
        return 2
    if "CPUExecutionProvider" not in onnxruntime.get_available_providers():
        print("CPUExecutionProvider ausente", file=sys.stderr)
        return 3
    page = fixture()
    page.get_layout()
    caixas = page.layout_information or []
    if not caixas:
        print("layout sem caixas", file=sys.stderr)
        return 4
    print(json.dumps({"pymupdf": pymupdf.__version__, "onnxruntime": onnxruntime.__version__, "caixas": len(caixas), "pesos": len(inventario)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
