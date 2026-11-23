# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Gera docs/pre-registro/corpus-manifesto.json (pré-registro §2.5) a partir da Camada B baixada.

Uso: gerar_corpus_manifesto.py BASE_CAMADA_B [--saida ARQ]
BASE_CAMADA_B contém opendataloader-bench/ e olmocr-bench/ (baixados por revisão fixada, sem execução de código).
Por arquivo: camada, fonte, estrato, papel (documento | gabarito | licenca), SHA-256, bytes, origem, revisão,
licença e divisão. Divisão (§1.4): por fonte, documentos ordenados por SHA-256; o primeiro terço (piso) é
calibração, o restante teste. Determinístico: mesma árvore, mesmos bytes. Não lê conteúdo além dos bytes para o hash
e, no olmOCR, do campo `pdf` dos arquivos de teste para ligar teste a documento.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

FONTES = {
    "opendataloader-bench": {
        "origem": "https://github.com/opendataloader-project/opendataloader-bench",
        "revisao": "7af1d8f4d0c09f51ea1a5c6ba5f66e993286d109",
        "licenca": "código Apache-2.0; PDFs e gabarito DP-Bench (upstage/dp-bench), MIT",
        "metricas": ["NID", "TEDS", "MHS"],
    },
    "olmocr-bench": {
        "origem": "https://huggingface.co/datasets/allenai/olmOCR-bench",
        "revisao": "54a96a6fb6a2bd3b297e59869491db4d3625b711",
        "licenca": "ODC-BY-1.0",
        "metricas": ["ordem", "mobiliário", "tabela", "texto (pass/fail)"],
    },
}
ESTRATO_OLM = {"multi_column": "duas_colunas", "old_scans": "escaneado", "tables": "tabelas",
               "table_tests": "tabelas", "headers_footers": "cabecalho_corrido"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def item(base: Path, p: Path, fonte: str, estrato: str, papel: str) -> dict:
    meta = FONTES[fonte]
    return {"camada": "B", "fonte": fonte, "estrato": estrato, "papel": papel,
            "arquivo": p.relative_to(base).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size,
            "origem": meta["origem"], "revisao": meta["revisao"], "licenca": meta["licenca"]}


def dividir(itens: list[dict]) -> None:
    docs = sorted((i for i in itens if i["papel"] == "documento"), key=lambda i: (i["sha256"], i["arquivo"]))
    corte = len(docs) // 3
    for k, i in enumerate(docs):
        i["divisao"] = "calibracao" if k < corte else "teste"
    for i in itens:
        i.setdefault("divisao", None)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", type=Path)
    ap.add_argument("--saida", type=Path, default=Path(__file__).with_name("corpus-manifesto.json"))
    a = ap.parse_args(argv)
    base = a.base.resolve()
    itens: list[dict] = []
    odl = base / "opendataloader-bench"
    grupo = [item(base, p, "opendataloader-bench", "dp-bench", "documento") for p in sorted((odl / "pdfs").rglob("*.pdf"))]
    grupo += [item(base, p, "opendataloader-bench", "dp-bench", "gabarito") for p in sorted((odl / "ground-truth").rglob("*")) if p.is_file()]
    grupo += [item(base, odl / n, "opendataloader-bench", "-", "licenca") for n in ("LICENSE", "THIRD_PARTY_NOTICES.md") if (odl / n).exists()]
    dividir(grupo)
    itens += grupo
    olm = base / "olmocr-bench"
    grupo = []
    for p in sorted((olm / "bench_data" / "pdfs").rglob("*.pdf")):
        cat = p.relative_to(olm / "bench_data" / "pdfs").parts[0]
        grupo.append(item(base, p, "olmocr-bench", ESTRATO_OLM.get(cat, cat), "documento"))
    grupo += [item(base, p, "olmocr-bench", ESTRATO_OLM.get(p.stem, p.stem), "gabarito") for p in sorted((olm / "bench_data").glob("*.jsonl"))]
    if (olm / "README.md").exists():
        grupo.append(item(base, olm / "README.md", "olmocr-bench", "-", "licenca"))
    dividir(grupo)
    itens += grupo
    contagem: dict = {}
    for i in itens:
        if i["papel"] == "documento":
            c = contagem.setdefault(f"{i['fonte']}/{i['estrato']}", {"calibracao": 0, "teste": 0})
            c[i["divisao"]] += 1
    manifesto = {
        "titulo": "Manifesto do corpus do pré-registro da 3.0.0 (§2.5)",
        "autor": "Ricardo Peres Freoa | Stocche Forbes Advogados",
        "regra_divisao": "por fonte, documentos ordenados por SHA-256 do arquivo; primeiro terço (piso) calibração, restante teste",
        "camada_a": {"estado": "pendente",
                     "regra": "gerada por script versionado em benchmarks/corpus-a/ (E3), com gabarito por construção; "
                              "seu manifesto entra por adendo datado deste arquivo antes da primeira medição de candidata, "
                              "e os casos críticos C1-C26 ficam fora das amostras de 300/200 (emenda D3)"},
        "fontes": FONTES,
        "contagem_documentos": dict(sorted(contagem.items())),
        "arquivos": itens,
    }
    a.saida.write_bytes((json.dumps(manifesto, ensure_ascii=False, indent=1, sort_keys=False) + "\n").encode("utf-8"))
    print(json.dumps(manifesto["contagem_documentos"], ensure_ascii=False))
    print(len(itens), "arquivos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
