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
         "labels": {"declared": None, "observed": "12", "inferred": "12", "state": "inferred", "divergence": None},
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


def sem_geometria() -> dict:
    """Fonte sem geometria (DOCX): página sem dimensões, bloco sem caixa (emenda técnica de 23/11/2026)."""
    t = "Parágrafo de documento sem geometria de página."
    b = bloco("docx-par", 1, 0, t, "unknown", engine="python-docx")
    b["bbox"] = None
    return {
        "schema_version": 2, "source_name": "exemplo-sintetico.docx", "source_sha256": "0" * 64, "converter_version": "3.0.0.dev0",
        "status": "complete", "fidelity": "unverified",
        "config": {"config_sha256": "1" * 64, "options": {}},
        "engines": [{"name": "classic", "version": "3.0.0.dev0", "role": "layout", "models": []}],
        "representation": {"markdown_file": "document.md", "markdown_sha256": "2" * 64, "offset_unit": "unicode_code_points", "interval": "half_open"},
        "pages": [{"index": 1, "width": None, "height": None, "rotation": 0, "route": "native",
                   "labels": {"declared": None, "observed": None, "inferred": None, "state": "unknown"},
                   "blocks": [b], "quality_signals": {}, "warnings": []}],
        "sections": [], "notes": [], "relations": [],
        "render_map": [{"char_start": 26, "char_end": 26 + len(t), "block_id": b["id"], "transform": "literal",
                        "source_start": 0, "source_end": len(t)}],
        "warnings": []}


def invalidos_sem_geometria(base: dict) -> dict[str, tuple[str, dict]]:
    a = copy.deepcopy(base)
    a["pages"][0]["blocks"][0]["bbox"] = [72.0, 90.0, 523.0, 120.0]
    b = copy.deepcopy(base)
    b["pages"][0]["width"] = 595
    return {"sem-geometria-com-caixa": ("pagina_sem_geometria_com_caixa", a),
            "geometria-de-pagina-parcial": ("geometria_de_pagina_parcial", b)}


MD_CHUNKS = "# exemplo-sintetico.pdf\n\nCapítulo I\n\nA responsabilidade continua, conforme a obrigação1 do contrato.\n\n1 Nota de rodapé sintética.\n"


def exemplos_chunks(base: dict) -> tuple[list[dict], dict[str, tuple[str, list[dict]]]]:
    """Chunks v2 sobre MD_CHUNKS, ligados a minimo.json (notes): um contíguo e um composto válidos e um inválido por invariante."""
    md = MD_CHUNKS
    h = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()
    src, mds = base["source_sha256"], h(md)
    nota = base["notes"][0]["id"]
    comum = {"schema_version": 2, "total": 2, "source_name": base["source_name"], "source_sha256": src,
             "markdown_sha256": mds, "source_markdown": "document.md", "asset_base": "", "offset_unit": "unicode_code_points",
             "target_size_chars": 6000, "requested_overlap_chars": 0, "actual_overlap_chars": 0, "overlap_adjustment": None,
             "headings": [], "headings_in_chunk": [], "pages": [1], "blocks": [], "document_status": "partial",
             "fidelity": "unverified", "warnings": []}
    a, b = md.index("Capítulo"), md.index("1 Nota") - 2
    t0 = md[a:b]
    c0 = dict(comum, index=1, chunk_id=h(f"{src}:{mds}:{a}:{b}"), char_start=a, char_end=b, char_count=b - a, text=t0,
              text_sha256=h(t0), context={"section_path": ["Capítulo I"], "printed_pages": [], "footnote_ids": [nota]},
              printed_pages=[], footnote_ids=[nota])
    s1 = (md.index("A respons"), md.index("1 Nota") - 2)
    s2 = (md.index("1 Nota"), len(md) - 1)
    t1 = "\n\n".join(md[x:y] for x, y in (s1, s2))
    c1 = dict(comum, index=2, chunk_id=h(f"{src}:{mds}:spans:{s1[0]}-{s1[1]};{s2[0]}-{s2[1]}"),
              spans=[{"char_start": s1[0], "char_end": s1[1]}, {"char_start": s2[0], "char_end": s2[1]}],
              envelope_char_start=s1[0], envelope_char_end=s2[1], text=t1, text_sha256=h(t1),
              context={"section_path": ["Capítulo I"], "printed_pages": [], "footnote_ids": [nota]},
              printed_pages=[], footnote_ids=[nota],
              embed_text="Capítulo I\n\n" + t1, embed_sha256=h("Capítulo I\n\n" + t1), embed_synthetic=True)
    validos = [c0, c1]
    casos: dict[str, tuple[str, list[dict]]] = {}

    def caso(nome, codigo, k, f):
        v = copy.deepcopy(validos)
        f(v[k])
        casos[nome] = (codigo, v)

    caso("chunk-footnote-id-inexistente", "footnote_id_inexistente", 0, lambda c: (c.__setitem__("footnote_ids", [hid("n", "fantasma")]), c["context"].__setitem__("footnote_ids", [hid("n", "fantasma")])))
    caso("chunk-footnote-id-malformado", "schema", 0, lambda c: c.__setitem__("footnote_ids", ["n-xyz"]))
    caso("chunk-context-diverge", "context_diverge_do_topo", 0, lambda c: c["context"].__setitem__("footnote_ids", []))
    caso("chunk-texto-diverge", "texto_diverge_do_intervalo", 0, lambda c: c.__setitem__("char_end", c["char_end"] - 1))
    caso("chunk-id-errado", "chunk_id", 0, lambda c: c.__setitem__("chunk_id", "f" * 64))
    caso("chunk-text-sha", "text_sha256", 1, lambda c: c.__setitem__("text_sha256", "e" * 64))
    caso("chunk-spans-fora-de-ordem", "spans_fora_de_ordem_ou_do_envelope", 1, lambda c: c.__setitem__("spans", list(reversed(c["spans"]))))
    caso("chunk-composto-com-char-start", "schema", 1, lambda c: c.__setitem__("char_start", 0))
    caso("chunk-embed-sha", "embed_sha256", 1, lambda c: c.__setitem__("embed_sha256", "d" * 64))
    caso("chunk-indice", "indice_ou_total", 1, lambda c: c.__setitem__("index", 5))
    caso("chunk-hash-de-origem", "hash_de_origem", 0, lambda c: c.__setitem__("markdown_sha256", "c" * 64))
    caso("chunk-envelope-inexato", "envelope_inexato", 1, lambda c: c.__setitem__("envelope_char_start", c["envelope_char_start"] - 1))
    caso("chunk-texto-diverge-dos-spans", "texto_diverge_dos_spans", 1, lambda c: c.__setitem__("text", c["text"].replace("\n\n", "\n", 1)))
    caso("chunk-pagina-impressa-inferida", "pagina_impressa_nao_confirmada", 0, lambda c: (c.__setitem__("printed_pages", ["12"]), c["context"].__setitem__("printed_pages", ["12"])))
    return validos, casos


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
    fantasma = hid("b", "fantasma")
    caso("block-id-duplicado", "block_id_duplicado", lambda d, b: b[B5].__setitem__("id", B4))
    caso("anchor-id-duplicado", "anchor_id_duplicado", lambda d, b: b[B6]["anchors"][0].__setitem__("id", hid("a", "chamada-1")))
    caso("note-id-duplicado", "note_id_duplicado", lambda d, b: d["notes"].append(copy.deepcopy(d["notes"][0])))
    caso("parte-de-nota-inexistente", "parte_de_nota_inexistente", lambda d, b: d["notes"][0].__setitem__("parts", [fantasma]))
    caso("relacao-com-bloco-inexistente", "relacao_com_bloco_inexistente", lambda d, b: d["relations"][1].__setitem__("to", fantasma))
    caso("footnote-call-sem-largura", "footnote_call_sem_largura", lambda d, b: b[B2]["anchors"][0].__setitem__("text_end", b[B2]["anchors"][0]["text_start"]))
    caso("anchored-in-ancora-invalida", "anchored_in_ancora_invalida", lambda d, b: d["relations"][2].__setitem__("from_anchor", hid("a", "chamada-1")))
    caso("render-map-fora-de-ordem", "render_map_fora_de_ordem", lambda d, b: d["render_map"].insert(0, d["render_map"].pop(1)))
    caso("render-map-bloco-inexistente", "render_map_bloco_inexistente", lambda d, b: d["render_map"][0].__setitem__("block_id", fantasma))
    caso("render-map-fonte-fora-do-texto", "render_map_fonte_fora_do_texto", lambda d, b: d["render_map"][4].__setitem__("source_end", 999))
    p1 = lambda d: d["pages"][0]
    caso("pagina-do-bloco-divergente", "pagina_do_bloco_divergente", lambda d, b: b[B3].__setitem__("page", 1))
    caso("parte-de-nota-nao-footnote", "parte_de_nota_nao_footnote", lambda d, b: b[B3].__setitem__("role", "body"))
    caso("bloco-em-duas-notas", "bloco_em_duas_notas", lambda d, b: d["notes"].append(dict(copy.deepcopy(d["notes"][0]), id=hid("n", "nota-2"))))
    caso("ancora-com-notas-distintas", "ancora_com_notas_distintas", lambda d, b: (
        d["notes"].append({"id": hid("n", "nota-b"), "mark": d["notes"][0]["mark"], "parts": [B5], "state": "confirmed"}),
        b[B5].__setitem__("role", "footnote"),
        d["relations"].append(dict(copy.deepcopy(d["relations"][0]), id=hid("r", "link-b"), to=B5, to_note=hid("n", "nota-b")))))
    caso("ancora-sobre-gerado", "ancora_sobre_gerado", lambda d, b: b[B2]["anchors"][0].update(
        text_start=b[B2]["generated"][0]["text_start"], text_end=b[B2]["generated"][0]["text_end"]))
    caso("render-map-omite-texto", "render_map_omite_texto", lambda d, b: d["render_map"][4].update(
        source_start=3, char_start=d["render_map"][4]["char_start"] + 3))
    caso("render-map-fonte-duplicada", "render_map_fonte_duplicada", lambda d, b: d["render_map"].append(dict(
        d["render_map"][-1], char_start=d["render_map"][-1]["char_end"] + 2, char_end=2 * d["render_map"][-1]["char_end"] - d["render_map"][-1]["char_start"] + 2)))
    caso("secao-com-referencia-invalida", "secao_com_referencia_invalida", lambda d, b: d["sections"][0]["block_ids"].append(fantasma))
    caso("rotulo-confirmado-sem-valor", "rotulo_confirmado_sem_valor", lambda d, b: p1(d)["labels"].update(observed=None, state="confirmed"))
    caso("rotulo-inferido-sem-valor", "rotulo_inferido_sem_valor", lambda d, b: p1(d)["labels"].__setitem__("inferred", None))
    caso("rotulo-divergente-sem-registro", "rotulo_divergente_sem_registro", lambda d, b: p1(d)["labels"].__setitem__("state", "divergent"))
    caso("pagina-escaneada-confirmada", "pagina_escaneada_confirmada", lambda d, b: (
        p1(d)["labels"].__setitem__("state", "confirmed"),
        p1(d)["quality_signals"].__setitem__("page_image", {"in_source": True, "preview_exported": False})))
    caso("ligacao-confirmada-em-scan", "ligacao_confirmada_em_scan", lambda d, b: p1(d).__setitem__("route", "ocr"))
    caso("fragmentos-fora-de-ordem", "fragmentos_fora_de_ordem_ou_pagina", lambda d, b: b[B2]["fragments"].reverse())
    caso("fragmento-pagina-inexistente", "fragmentos_fora_de_ordem_ou_pagina", lambda d, b: b[B2]["fragments"][1].__setitem__("page", 99))
    caso("ancora-nao-localizada-fora-do-bloco", "ancora_nao_localizada_fora_do_bloco", lambda d, b: b[B6]["anchors"][1].__setitem__("page", 1))
    caso("object-position-sem-anchored-in", "object_position_sem_anchored_in", lambda d, b: d["relations"].pop(2))
    return casos


def variantes(base: dict) -> dict:
    """Válido: emendas de 23/11 em página com geometria (page_image presente; figura sem caixa observada)."""
    d = copy.deepcopy(base)
    d["pages"][1]["quality_signals"]["page_image"] = {"in_source": False, "preview_exported": False}
    blocos(d)[hid("b", "figura")]["bbox"] = None
    return d


def main() -> int:
    base = valido()
    for sub in ("validos", "invalidos"):
        (RAIZ / sub).mkdir(parents=True, exist_ok=True)

    def escrever(p, d):
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")

    escrever(RAIZ / "validos" / "minimo.json", base)
    sg = sem_geometria()
    escrever(RAIZ / "validos" / "sem-geometria.json", sg)
    escrever(RAIZ / "validos" / "variantes.json", variantes(base))
    esperado = {}
    for nome, (codigo, d) in {**invalidos(base), **invalidos_sem_geometria(sg)}.items():
        escrever(RAIZ / "invalidos" / f"{nome}.json", d)
        esperado[f"{nome}.json"] = codigo
    escrever(RAIZ / "esperado.json", esperado)
    ck_dir = RAIZ / "chunks"
    (ck_dir / "invalidos").mkdir(parents=True, exist_ok=True)
    (ck_dir / "document.md").write_bytes(MD_CHUNKS.encode("utf-8"))

    def jsonl(p, linhas):
        p.write_bytes("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas).encode("utf-8"))

    validos_ck, casos_ck = exemplos_chunks(base)
    jsonl(ck_dir / "valido.jsonl", validos_ck)
    esperado_ck = {}
    for nome, (codigo, linhas) in casos_ck.items():
        jsonl(ck_dir / "invalidos" / f"{nome}.jsonl", linhas)
        esperado_ck[f"{nome}.jsonl"] = codigo
    escrever(ck_dir / "esperado.json", esperado_ck)
    print(len(esperado), "inválidos;", len(esperado_ck), "chunks inválidos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
