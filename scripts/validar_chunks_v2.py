# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Valida chunks.jsonl v2 contra o contrato (estrutura) e as invariantes cruzadas com document.md e document.json v2.

Uso: validar_chunks_v2.py CHUNKS.jsonl DOCUMENT.md DOCUMENT_V2.json ; sai 0 se válido, 1 se não.
Cada erro sai como "<arquivo>: <código>: <detalhe>". Códigos estáveis (os exemplos inválidos os declaram).
Estrutura exige `jsonschema`; sem ele, só as invariantes rodam e o relatório o declara.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "contratos" / "chunks-v2.schema.json"


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def estrutura(chunk: dict) -> list[tuple[str, str]] | None:
    try:
        import jsonschema
    except ImportError:
        return None
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    return [("schema", f"{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}") for e in v.iter_errors(chunk)]


def invariantes(chunks: list[dict], md: str, doc: dict) -> list[tuple[str, str]]:
    erros: list[tuple[str, str]] = []
    md_sha = sha(md)
    notas = {n["id"] for n in doc.get("notes", [])}
    confirmados = {}
    for p in doc.get("pages", []):
        lab = p.get("labels", {})
        if lab.get("state") == "confirmed":
            confirmados[p["index"]] = {x for x in (lab.get("declared"), lab.get("observed")) if x is not None}
    for k, c in enumerate(chunks):
        r = f"chunk {k}"
        if type(c["index"]) is not int or c["index"] != k + 1 or c["total"] != len(chunks):
            erros.append(("indice_ou_total", r))
        if c["source_sha256"] != doc["source_sha256"] or c["markdown_sha256"] != md_sha:
            erros.append(("hash_de_origem", r))
        if c["text_sha256"] != sha(c["text"]):
            erros.append(("text_sha256", r))
        if "spans" in c:
            spans = [(s["char_start"], s["char_end"]) for s in c["spans"]]
            fim = c["envelope_char_start"]
            for a, b in spans:
                if a < fim or b < a or b > c["envelope_char_end"]:
                    erros.append(("spans_fora_de_ordem_ou_do_envelope", r))
                    break
                fim = b
            if spans and (spans[0][0] != c["envelope_char_start"] or spans[-1][1] != c["envelope_char_end"]):
                erros.append(("envelope_inexato", r))
            if c["text"] != "\n\n".join(md[a:b] for a, b in spans):
                erros.append(("texto_diverge_dos_spans", r))
            base = ";".join(f"{a}-{b}" for a, b in spans)
            ident = sha(f"{c['source_sha256']}:{c['markdown_sha256']}:spans:{base}")
        else:
            a, b = c["char_start"], c["char_end"]
            if md[a:b] != c["text"] or c["char_count"] != b - a or b > len(md):
                erros.append(("texto_diverge_do_intervalo", r))
            ident = sha(f"{c['source_sha256']}:{c['markdown_sha256']}:{a}:{b}")
        if c["chunk_id"] != ident:
            erros.append(("chunk_id", r))
        if set(c["footnote_ids"]) - notas:
            erros.append(("footnote_id_inexistente", r))
        if c["context"]["footnote_ids"] != c["footnote_ids"] or c["context"]["printed_pages"] != c["printed_pages"]:
            erros.append(("context_diverge_do_topo", r))
        validos = set().union(*(confirmados.get(p, set()) for p in c["pages"]))
        if set(c["printed_pages"]) - validos:
            erros.append(("pagina_impressa_nao_confirmada", r))
        if "embed_text" in c and c["embed_sha256"] != sha(c["embed_text"]):
            erros.append(("embed_sha256", r))
    return erros


def main(argv: list[str]) -> int:
    arq_ck, arq_md, arq_doc = map(Path, argv)
    md = arq_md.read_bytes().decode("utf-8")
    doc = json.loads(arq_doc.read_text(encoding="utf-8"))
    chunks = [json.loads(x) for x in arq_ck.read_text(encoding="utf-8").splitlines() if x.strip()]
    erros: list[tuple[str, str]] = []
    sem_schema = False
    for k, c in enumerate(chunks):
        est = estrutura(c)
        if est is None:
            sem_schema = True
            continue
        erros += [(cod, f"chunk {k}: {det}") for cod, det in est]
    if sem_schema:
        print(f"{arq_ck}: aviso: jsonschema ausente, estrutura não verificada")
    if not erros:
        try:
            erros = invariantes(chunks, md, doc)
        except (KeyError, TypeError) as exc:
            erros = [("estrutura_incompleta", repr(exc))]
    for cod, det in erros:
        print(f"{arq_ck}: {cod}: {det}")
    return 1 if erros else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
