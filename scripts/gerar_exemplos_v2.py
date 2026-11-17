# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Gera os exemplos sintéticos do contrato v2 (emenda D2): um válido e um inválido por invariante.

Saída em docs/contratos/exemplos/{validos,invalidos}/ e esperado.json (código esperado por inválido).
Determinístico: rodar de novo produz os mesmos bytes.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1] / "docs" / "contratos" / "exemplos"


def hid(prefixo: str, rotulo: str) -> str:
    return f"{prefixo}-" + hashlib.sha256(rotulo.encode()).hexdigest()[:32]


def bloco(rot, page, ordinal, text, role, level=None, **extra):
    b = {"id": hid("b", rot), "page": page, "ordinal": ordinal, "bbox": [72.0, 90.0 + 40 * ordinal, 523.0, 120.0 + 40 * ordinal],
         "coord_system": "pdf_points_top_left", "text": text, "role": role, "level": level, "engine": "classic", "warnings": []}
    b.update(extra)
    return b


def valido() -> dict:
    t2 = "a responsabi-\nlidade continua, conforme a obrigação1 do contrato."
    i_hifen = t2.index("-\n")
    i_chamada = t2.index("1 do")
    t6 = "Antes da figura. Depois da figura, com chamada sem posição segura."
    corte6 = t6.index("Depois")
    b1 = bloco("cap", 1, 0, "Capítulo I", "heading", level=1)
    b2 = bloco("par-transpaginal", 1, 1, t2, "body",
               fragments=[{"page": 1, "bbox": [72.0, 708.0, 130.0, 723.0], "block_id": hid("b", "par-transpaginal"), "text_start": 0, "text_end": i_hifen + 1},
                          {"page": 2, "bbox": [72.0, 50.0, 400.0, 65.0], "block_id": hid("b", "par-transpaginal"), "text_start": i_hifen + 2, "text_end": len(t2)}],
               generated=[{"text_start": i_hifen + 1, "text_end": i_hifen + 2, "transform": "soft_hyphen_join"}],
               anchors=[{"id": hid("a", "chamada-1"), "kind": "footnote_call", "located": True, "text_start": i_chamada, "text_end": i_chamada + 1,
                         "mark": {"printed": "1", "form": "digits", "presentation": "superscript", "number": 1},
                         "evidence": ["superscript", "smaller_font"], "confidence": 0.97}])
    b3 = bloco("nota-1", 2, 0, "1 Nota de rodapé sintética.", "footnote")
    b4 = bloco("figura", 2, 1, "", "figure", asset="assets/figura-1.png")
    b5 = bloco("legenda", 2, 2, "Figura 1 - Exemplo.", "caption")
    b6 = bloco("par-interrompido", 2, 3, t6, "body",
               fragments=[{"page": 2, "bbox": [72.0, 200.0, 523.0, 215.0], "block_id": hid("b", "par-interrompido"), "text_start": 0, "text_end": corte6 - 1},
                          {"page": 2, "bbox": [72.0, 400.0, 523.0, 415.0], "block_id": hid("b", "par-interrompido"), "text_start": corte6, "text_end": len(t6)}],
               generated=[{"text_start": corte6 - 1, "text_end": corte6, "transform": "geometry_continuation"}],
               anchors=[{"id": hid("a", "posicao-figura"), "kind": "object_position", "located": True, "text_start": corte6, "text_end": corte6,
                         "evidence": [], "confidence": 0.9},
                        {"id": hid("a", "chamada-sem-posicao"), "kind": "footnote_call", "located": False, "text_start": None, "text_end": None,
                         "page": 2, "bbox": [300.0, 410.0, 305.0, 416.0],
                         "mark": {"printed": "*", "form": "asterisk", "presentation": "unknown", "number": None},
                         "evidence": ["smaller_font"], "confidence": None}])
    b6["warnings"] = ["unlocated_footnote_call"]
    paginas = [
        {"index": 1, "width": 595, "height": 842, "rotation": 0, "route": "native",
         "labels": {"declared": None, "observed": "12", "inferred": None, "state": "inferred", "divergence": None},
         "blocks": [b1, b2], "quality_signals": {}, "warnings": []},
        {"index": 2, "width": 595, "height": 842, "rotation": 0, "route": "native",
         "labels": {"declared": None, "observed": "13", "inferred": "13", "state": "inferred", "divergence": None},
         "blocks": [b3, b4, b5, b6], "quality_signals": {}, "warnings": []}]
    render, pos = [], 0
    for b, tr, rem in ((b1, "heading_mark", None), (b2, "soft_hyphen_join", [i_hifen, i_hifen + 2]), (b6, "literal", None),
                       (b4, "image_ref", None), (b5, "literal", None), (b3, "literal", None)):
        n = len(b["text"])
        tam = {"heading_mark": n + 2, "image_ref": 24}.get(tr, n - (rem[1] - rem[0] if rem else 0))
        ri = {"char_start": pos, "char_end": pos + tam, "block_id": b["id"], "transform": tr, "source_start": 0, "source_end": n}
        if rem:
            ri["removed"] = [{"text_start": rem[0], "text_end": rem[1]}]
        render.append(ri)
        pos += tam + 2
    nota = {"id": hid("n", "nota-1"), "mark": {"printed": "1", "form": "digits", "presentation": "inline", "number": 1}, "parts": [b3["id"]], "state": "confirmed"}
    rel = lambda rot, tipo, de, para, **kw: dict({"id": hid("r", rot), "type": tipo, "from": de, "to": para, "state": "confirmed",
                                                  "method": "sintetico", "confidence": 1.0, "evidence": ["geometry"]}, **kw)
    return {
        "schema_version": 2, "source_name": "exemplo-sintetico.pdf", "source_sha256": "0" * 64, "converter_version": "3.0.0.dev0",
        "status": "partial", "fidelity": "unverified",
        "config": {"config_sha256": "1" * 64, "options": {}},
        "engines": [{"name": "classic", "version": "3.0.0.dev0", "role": "layout", "models": []}],
        "representation": {"markdown_file": "document.md", "markdown_sha256": "2" * 64, "offset_unit": "unicode_code_points", "interval": "half_open"},
        "pages": paginas,
        "sections": [{"id": hid("s", "cap"), "level": 1, "title_block_id": b1["id"], "block_ids": [b["id"] for b in (b1, b2, b3, b4, b5, b6)],
                      "char_start": 0, "char_end": pos - 2, "children": []}],
        "notes": [nota],
        "relations": [rel("link-1", "footnote_link", b2["id"], b3["id"], from_anchor=hid("a", "chamada-1"), to_note=nota["id"]),
                      rel("legenda", "caption_of", b5["id"], b4["id"]),
                      rel("figura-no-fluxo", "anchored_in", b6["id"], b4["id"], from_anchor=hid("a", "posicao-figura"))],
        "render_map": render, "warnings": []}


def blocos(d):
    return {b["id"]: b for p in d["pages"] for b in p["blocks"]}


def invalidos(base: dict) -> dict[str, tuple[str, dict]]:
    casos = {}

    def caso(nome, codigo, f):
        d = copy.deepcopy(base)
        f(d, blocos(d))
        casos[nome] = (codigo, d)

    B2, B3, B4, B5, B6 = (hid("b", x) for x in ("par-transpaginal", "nota-1", "figura", "legenda", "par-interrompido"))
    caso("particao-lacuna", "particao_block_text", lambda d, b: b[B2]["fragments"][1].__setitem__("text_start", b[B2]["fragments"][1]["text_start"] + 1))
    caso("particao-sobreposicao", "particao_block_text", lambda d, b: b[B2]["generated"][0].__setitem__("text_start", b[B2]["generated"][0]["text_start"] - 1))
    caso("generated-sem-fragmentos", "generated_sem_fragmentos", lambda d, b: b[B6].pop("fragments"))
    caso("fragmento-de-outro-bloco", "fragmento_de_outro_bloco", lambda d, b: b[B2]["fragments"][0].__setitem__("block_id", B6))
    caso("object-position-com-largura", "object_position_com_largura", lambda d, b: b[B6]["anchors"][0].__setitem__("text_end", b[B6]["anchors"][0]["text_end"] + 1))
    caso("ancora-fora-do-texto", "ancora_fora_do_texto", lambda d, b: b[B2]["anchors"][0].update(text_start=999, text_end=1000))
    caso("ancora-nao-localizada-com-offset", "schema", lambda d, b: b[B6]["anchors"][1].update(text_start=3, text_end=4))
    caso("ancora-nao-localizada-sem-bbox", "schema", lambda d, b: b[B6]["anchors"][1].pop("bbox"))
    caso("object-position-com-mark", "schema", lambda d, b: b[B6]["anchors"][0].__setitem__("mark", b[B2]["anchors"][0]["mark"]))
    caso("role-footnote-ref", "schema", lambda d, b: b[B3].__setitem__("role", "footnote_ref"))
    caso("footnote-link-sem-to-note", "schema", lambda d, b: d["relations"][0].pop("to_note"))
    caso("footnote-link-para-nao-primeira-parte", "footnote_link_nota_invalida", lambda d, b: d["relations"][0].__setitem__("to", B5))
    caso("footnote-link-de-object-position", "footnote_link_ancora_invalida", lambda d, b: d["relations"][0].update({"from": B6, "from_anchor": hid("a", "posicao-figura")}))
    caso("anchored-in-para-nao-figura", "anchored_in_destino_nao_figura", lambda d, b: d["relations"][2].__setitem__("to", B5))
    caso("caption-of-sem-legenda", "caption_of_sem_legenda", lambda d, b: d["relations"][1].__setitem__("from", B3))
    caso("nota-com-continuacao-candidata", "parte_sem_continuacao_confirmada", lambda d, b: (
        d["notes"][0]["parts"].append(B5),
        d["relations"].append({"id": hid("r", "cont"), "type": "continuation", "from": B3, "to": B5, "state": "candidate",
                               "method": "sintetico", "confidence": 0.5, "evidence": ["page_sequence"]})))
    caso("sem-aviso-unlocated", "aviso_unlocated_ausente", lambda d, b: b[B6].__setitem__("warnings", []))
    caso("sem-aviso-unlinked", "aviso_unlinked_ausente", lambda d, b: d["relations"].pop(0))
    caso("removed-fora-da-fonte", "removed_fora_da_fonte", lambda d, b: d["render_map"][1]["removed"][0].update(text_start=0, text_end=999))
    caso("render-comprimento", "render_map_comprimento", lambda d, b: d["render_map"][2].__setitem__("char_end", d["render_map"][2]["char_end"] + 1))
    caso("removed-em-literal", "schema", lambda d, b: d["render_map"][2].__setitem__("removed", [{"text_start": 0, "text_end": 1}]))
    caso("render-sem-source", "schema", lambda d, b: d["render_map"][0].pop("source_start"))
    return casos


def main() -> int:
    base = valido()
    for sub in ("validos", "invalidos"):
        (RAIZ / sub).mkdir(parents=True, exist_ok=True)

    def escrever(p, d):
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    escrever(RAIZ / "validos" / "minimo.json", base)
    esperado = {}
    for nome, (codigo, d) in invalidos(base).items():
        escrever(RAIZ / "invalidos" / f"{nome}.json", d)
        esperado[f"{nome}.json"] = codigo
    escrever(RAIZ / "esperado.json", esperado)
    print(len(esperado), "inválidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
