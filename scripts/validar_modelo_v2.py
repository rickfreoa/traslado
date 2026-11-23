# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Valida document.json v2 contra o contrato (estrutura) e as invariantes da emenda D2 (09/11/2026).

Uso: validar_modelo_v2.py ARQ.json [...] ; sai 0 se todos válidos, 1 se algum inválido.
Estrutura exige `jsonschema` (job do CI de contratos); sem ele, só as invariantes rodam e o relatório o declara.
Cada erro sai como "<arquivo>: <código>: <detalhe>". Os códigos são estáveis: os exemplos inválidos os declaram.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "docs" / "contratos" / "modelo-v2.schema.json"
LITERAIS = {"literal", "line_join", "soft_hyphen_join"}


def estrutura(doc: dict) -> list[tuple[str, str]] | None:
    try:
        import jsonschema
    except ImportError:
        return None
    if not hasattr(jsonschema, "Draft202012Validator"):
        return None
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    return [("schema", f"{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}") for e in v.iter_errors(doc)]


def _intervalos_sem_lacuna(partes: list[tuple[int, int]], total: int) -> str | None:
    pos = 0
    for a, b in sorted(partes):
        if a != pos:
            return f"{'lacuna' if a > pos else 'sobreposicao'} em {min(a, pos)}"
        if b < a:
            return f"intervalo invertido {a}-{b}"
        pos = b
    return None if pos == total else f"cobertura termina em {pos}, texto tem {total}"


def invariantes(doc: dict) -> list[tuple[str, str]]:
    erros: list[tuple[str, str]] = []
    blocos, ancoras = {}, {}
    for page in doc.get("pages", []):
        sem_geo = page.get("width") is None or page.get("height") is None
        if sem_geo and (page.get("width") is not None or page.get("height") is not None):
            erros.append(("geometria_de_pagina_parcial", str(page.get("index"))))
        if sem_geo and any(b.get("bbox") is not None or b.get("fragments")
                           or any(a.get("bbox") is not None for a in b.get("anchors", []))
                           for b in page.get("blocks", [])):
            erros.append(("pagina_sem_geometria_com_caixa", str(page.get("index"))))
        for b in page.get("blocks", []):
            if b["id"] in blocos:
                erros.append(("block_id_duplicado", b["id"]))
            blocos[b["id"]] = b
            n = len(b["text"])
            frs, ger = b.get("fragments", []), b.get("generated", [])
            if ger and not frs:
                erros.append(("generated_sem_fragmentos", b["id"]))
            for f in frs:
                if f["block_id"] != b["id"]:
                    erros.append(("fragmento_de_outro_bloco", b["id"]))
            if frs:
                falha = _intervalos_sem_lacuna([(x["text_start"], x["text_end"]) for x in frs + ger], n)
                if falha:
                    erros.append(("particao_block_text", f"{b['id']}: {falha}"))
            for a in b.get("anchors", []):
                if a["id"] in ancoras:
                    erros.append(("anchor_id_duplicado", a["id"]))
                ancoras[a["id"]] = (a, b)
                if a["located"]:
                    s, e = a["text_start"], a["text_end"]
                    if not (isinstance(s, int) and isinstance(e, int) and 0 <= s <= e <= n):
                        erros.append(("ancora_fora_do_texto", a["id"]))
                    elif a["kind"] == "object_position" and s != e:
                        erros.append(("object_position_com_largura", a["id"]))
                    elif a["kind"] == "footnote_call" and s == e:
                        erros.append(("footnote_call_sem_largura", a["id"]))
    notas = {}
    for nota in doc.get("notes", []):
        if nota["id"] in notas:
            erros.append(("note_id_duplicado", nota["id"]))
        notas[nota["id"]] = nota
        for p in nota["parts"]:
            if p not in blocos:
                erros.append(("parte_de_nota_inexistente", f"{nota['id']}: {p}"))
    rels = doc.get("relations", [])
    continuacoes = {(r["from"], r["to"]) for r in rels if r["type"] == "continuation" and r["state"] == "confirmed"}
    for nota in notas.values():
        for x, y in zip(nota["parts"], nota["parts"][1:]):
            if (x, y) not in continuacoes:
                erros.append(("parte_sem_continuacao_confirmada", f"{nota['id']}: {x}->{y}"))
    ligadas = set()
    for r in rels:
        for k in ("from", "to"):
            if r[k] not in blocos:
                erros.append(("relacao_com_bloco_inexistente", f"{r['id']}.{k}"))
        if r["type"] == "footnote_link":
            a = ancoras.get(r.get("from_anchor"))
            if not a or a[0]["kind"] != "footnote_call" or a[1]["id"] != r["from"]:
                erros.append(("footnote_link_ancora_invalida", r["id"]))
            nota = notas.get(r.get("to_note"))
            if not nota or nota["parts"][0] != r["to"]:
                erros.append(("footnote_link_nota_invalida", r["id"]))
            ligadas.add(r.get("from_anchor"))
        elif r["type"] == "anchored_in":
            a = ancoras.get(r.get("from_anchor"))
            if not a or a[0]["kind"] != "object_position" or a[1]["id"] != r["from"]:
                erros.append(("anchored_in_ancora_invalida", r["id"]))
            if r["to"] in blocos and blocos[r["to"]]["role"] != "figure":
                erros.append(("anchored_in_destino_nao_figura", r["id"]))
        elif r["type"] == "caption_of" and r["from"] in blocos and blocos[r["from"]]["role"] != "caption":
            erros.append(("caption_of_sem_legenda", r["id"]))
    for aid, (a, b) in ancoras.items():
        if a["kind"] != "footnote_call":
            continue
        if not a["located"] and "unlocated_footnote_call" not in b["warnings"]:
            erros.append(("aviso_unlocated_ausente", aid))
        if a["located"] and aid not in ligadas and "unlinked_footnote_call" not in b["warnings"]:
            erros.append(("aviso_unlinked_ausente", aid))
    fim = 0
    for i, ri in enumerate(doc.get("render_map", [])):
        b = blocos.get(ri["block_id"])
        if ri["char_start"] < fim or ri["char_end"] < ri["char_start"]:
            erros.append(("render_map_fora_de_ordem", str(i)))
        fim = max(fim, ri["char_end"])
        if b is None:
            erros.append(("render_map_bloco_inexistente", str(i)))
            continue
        s, e = ri["source_start"], ri["source_end"]
        if not 0 <= s <= e <= len(b["text"]):
            erros.append(("render_map_fonte_fora_do_texto", str(i)))
            continue
        removidos = ri.get("removed", [])
        pos = s
        for x in removidos:
            if not pos <= x["text_start"] <= x["text_end"] <= e:
                erros.append(("removed_fora_da_fonte", str(i)))
            pos = x["text_end"]
        if ri["transform"] in LITERAIS:
            esperado = (e - s) - sum(x["text_end"] - x["text_start"] for x in removidos)
            if ri["char_end"] - ri["char_start"] != esperado:
                erros.append(("render_map_comprimento", f"{i}: {ri['char_end'] - ri['char_start']} != {esperado}"))
    return erros


def main(argv: list[str]) -> int:
    ruim = False
    for nome in argv:
        doc = json.loads(Path(nome).read_text(encoding="utf-8"))
        est = estrutura(doc)
        if est is None:
            print(f"{nome}: aviso: jsonschema ausente, estrutura não verificada")
            est = []
        try:
            inv = invariantes(doc) if not est else []
        except (KeyError, TypeError) as exc:
            inv = [("estrutura_incompleta", repr(exc))]
        for codigo, detalhe in est + inv:
            print(f"{nome}: {codigo}: {detalhe}")
        ruim |= bool(est or inv)
    return 1 if ruim else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
