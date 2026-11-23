# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Demonstração da E0 (emenda pdf-craft, §3, item 4): a projeção `--schema 1` do motor `classic`
continua derivável do modelo v2 e inalterada.

Caminho provado, só com código da série 2.x:
  saída da 2.x (document.json v1, document.md, chunks.jsonl)
    -> modelo v2 (adaptador: blocos, render_map por alinhamento literal, ids determinísticos)
    -> validação (estrutura do JSON Schema e invariantes de `validar_modelo_v2.py`)
    -> Document v1 reconstruído **só do v2**
    -> render + chunks da 2.x -> comparação byte a byte com a saída original.

Uso:
  derivar_schema1.py saida  DIR_SAIDA [--pdf FONTE.pdf] --relatorio REL.json [--v2 V2.json]
  derivar_schema1.py exemplo V2.json --relatorio REL.json
Sai 0 se tudo idêntico e válido. Imprime e grava só hashes, contagens e códigos (sem texto).
Não é o adaptador de produção (E2): é a prova de que o contrato comporta a projeção.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

from traslado.model import Block, Document, Page  # noqa: E402
from traslado.render import layout  # noqa: E402
from traslado.review import review_text  # noqa: E402
from traslado import chunks as C  # noqa: E402
import validar_modelo_v2 as V  # noqa: E402

NEUTRAS = ("tesseract", "tessdata")  # caminhos absolutos: fora de config.options (campos neutralizados da C0)
OPERACIONAIS = ("job_timeout", "ocr_timeout", "ocr_workers", "advanced_timeout", "max_memory_mb", "overwrite")
REMOVIVEIS = set(" \t\n\r­")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def block_id(src: str, cfg: str, page: int, ordinal: int, bbox, text: str) -> str:
    caixa = "null" if bbox is None else ",".join(repr(float(x)) for x in bbox)
    base = f"{src}|{cfg}|{page}|{ordinal}|{caixa}|{sha(text.encode('utf-8'))}"
    return "b-" + sha(base.encode("utf-8"))[:32]


# ---------- v1 -> v2 ----------

def papel_v2(b: dict) -> str:
    if b["kind"] in ("table", "html_table"):
        return "table"
    if b["kind"] == "image":
        return "figure"
    return {"body": "body", "furniture": "furniture", None: "unknown"}[b.get("role")]


def alinhar(fonte: str, saida: str) -> list[tuple[int, int, int, int]] | None:
    """Corridas literais (fs, fe, ss, se) entre block.text e o trecho renderizado.

    A renderização da 2.x só remove espaço, quebra e hífen discricionário da fonte e só insere espaço.
    Devolve None se a saída não for explicável assim (o chamador acusa)."""
    i = j = 0
    corridas: list[list[int]] = []
    while j < len(saida):
        if i < len(fonte) and fonte[i] == saida[j]:
            if corridas and corridas[-1][1] == i and corridas[-1][3] == j:
                corridas[-1][1] += 1
                corridas[-1][3] += 1
            else:
                corridas.append([i, i + 1, j, j + 1])
            i += 1
            j += 1
        elif i < len(fonte) and fonte[i] in REMOVIVEIS:
            i += 1
        elif saida[j] == " ":
            j += 1
        else:
            return None
    if any(c not in REMOVIVEIS for c in fonte[i:]):
        return None
    return [tuple(c) for c in corridas]


def intervalos_bloco(bid: str, base: int, fonte: str, saida: str, perdas: dict) -> list[dict]:
    corr = alinhar(fonte, saida)
    if corr is None:
        perdas["render_inexplicavel"] = perdas.get("render_inexplicavel", 0) + 1
        return []
    out: list[dict] = []
    for fs, fe, ss, se in corr:
        ant = out[-1] if out else None
        lacuna_fonte = fonte[ant["source_end"]:fs] if ant else ""
        if ant and "­" in lacuna_fonte and ant["char_end"] == base + ss:
            # junção por hífen discricionário: a remoção fica declarada em `removed` (emenda D2)
            ant["transform"] = "soft_hyphen_join"
            ant.setdefault("removed", []).append({"text_start": ant["source_end"], "text_end": fs})
            ant["source_end"], ant["char_end"] = fe, base + se
        else:
            out.append({"char_start": base + ss, "char_end": base + se, "block_id": bid,
                        "transform": "literal", "source_start": fs, "source_end": fe})
    return out


def v1_para_v2(d1: dict, md: str, dims: dict[int, tuple[float, float, int]] | None) -> tuple[dict, dict]:
    perdas: dict[str, int] = {}
    opts = {k: v for k, v in d1["options"].items() if k not in NEUTRAS}
    cfg = sha(canon({k: v for k, v in opts.items() if k not in OPERACIONAIS}))
    doc = documento_v1(d1)
    rend = layout(doc)
    if rend.markdown != md:
        raise SystemExit("saida: document.md não é o render do document.json (fora do escopo desta prova)")
    paginas, ids = [], {}
    for p in d1["pages"]:
        w, h, rot = (dims or {}).get(p["number"], (None, None, 0))
        blocos = []
        for k, b in enumerate(p["blocks"]):
            bid = block_id(d1["source_sha256"], cfg, p["number"], k, b.get("bbox"), b.get("text", ""))
            ids[(p["number"], k + 1)] = bid
            nb = {"id": bid, "page": p["number"], "ordinal": k, "bbox": b.get("bbox"),
                  "coord_system": "pdf_points_top_left", "text": b.get("text", ""), "role": papel_v2(b),
                  "level": None, "engine": b["engine"], "confidence": b.get("confidence"),
                  "rows": b.get("rows"), "asset": b.get("asset"), "warnings": []}
            if nb["asset"] is None:
                nb.pop("asset")
            if b.get("details"):
                perdas["block.details"] = perdas.get("block.details", 0) + 1
            blocos.append(nb)
        if p.get("metrics"):
            perdas["page.metrics"] = perdas.get("page.metrics", 0) + 1
        if p.get("native_text") is not None:
            perdas["page.native_text"] = perdas.get("page.native_text", 0) + 1
        qs = {}
        mt = p.get("metrics") or {}
        if "scan_page_image_in_source_pdf" in mt or "scan_previews_exported" in mt:
            qs["page_image"] = {"in_source": bool(mt.get("scan_page_image_in_source_pdf")),
                                "preview_exported": bool(mt.get("scan_previews_exported"))}
        paginas.append({"index": p["number"], "width": w, "height": h, "rotation": rot, "route": p["route"],
                        "labels": {"declared": None, "observed": None, "inferred": None, "state": "unknown"},
                        "blocks": blocos, "quality_signals": qs, "warnings": list(p["warnings"])})
    rmap = []
    por_id = {b["id"]: b for pg in paginas for b in pg["blocks"]}
    for ref in rend.refs:
        bid = ids[(ref["page"], ref["block"])]
        b = por_id[bid]
        trecho = md[ref["char_start"]:ref["char_end"]]
        if ref["kind"] == "table":
            rmap.append({"char_start": ref["char_start"], "char_end": ref["char_end"], "block_id": bid,
                         "transform": "table_render", "source_start": 0, "source_end": len(b["text"])})
        elif ref["kind"] == "image":
            rmap.append({"char_start": ref["char_start"], "char_end": ref["char_end"], "block_id": bid,
                         "transform": "image_ref", "source_start": 0, "source_end": len(b["text"])})
        else:
            rmap.extend(intervalos_bloco(bid, ref["char_start"], b["text"], trecho, perdas))
    v2 = {"schema_version": 2, "source_name": d1["source_name"], "source_sha256": d1["source_sha256"],
          "converter_version": d1["converter_version"], "status": d1["status"], "fidelity": "unverified",
          "duration_seconds": d1.get("duration_seconds", 0),
          "config": {"config_sha256": cfg, "options": opts},
          "engines": [{"name": "classic", "version": d1["converter_version"], "role": "layout", "models": []}],
          "representation": {"markdown_file": "document.md", "markdown_sha256": sha(md.encode("utf-8")),
                             "offset_unit": "unicode_code_points", "interval": "half_open"},
          "pages": paginas, "sections": [], "notes": [], "relations": [], "render_map": rmap,
          "warnings": list(d1["warnings"])}
    return v2, perdas


# ---------- v2 -> Document v1 (só do v2) ----------

def documento_v1(d1: dict) -> Document:
    pages = [Page(number=p["number"], route=p["route"], blocks=[Block(**b) for b in p["blocks"]],
                  warnings=p["warnings"], metrics=p["metrics"], native_text=p.get("native_text"))
             for p in d1["pages"]]
    keys = {"source_name", "source_sha256", "warnings", "status", "schema_version", "converter_version",
            "duration_seconds", "options", "submotor", "submotor_version", "experimental"}
    return Document(pages=pages, **{k: v for k, v in d1.items() if k in keys})


def bloco_v1(b: dict) -> Block:
    r = b["role"]
    if r == "table":
        kind = "table" if b.get("rows") is not None else "html_table"
    elif r == "figure":
        kind = "image"
    else:
        kind = "text"
    role = {"body": "body", "furniture": "furniture"}.get(r) if kind == "text" else None
    return Block(kind=kind, text=b["text"], bbox=b["bbox"], asset=b.get("asset"), engine=b["engine"],
                 rows=b.get("rows"), confidence=b.get("confidence"), role=role)


def v2_para_documento(v2: dict) -> Document:
    def metricas(p: dict) -> dict:
        im = p["quality_signals"].get("page_image")
        return {} if im is None else {"scan_page_image_in_source_pdf": im["in_source"],
                                      "scan_previews_exported": im["preview_exported"]}
    pages = [Page(number=p["index"], route=p["route"],
                  blocks=[bloco_v1(b) for b in sorted(p["blocks"], key=lambda x: x["ordinal"])],
                  warnings=list(p["warnings"]), metrics=metricas(p)) for p in v2["pages"]]
    return Document(source_name=v2["source_name"], source_sha256=v2["source_sha256"], pages=pages,
                    warnings=list(v2["warnings"]), status=v2["status"],
                    converter_version=v2["converter_version"], options=dict(v2["config"]["options"]))


def projetar(v2: dict, destino: Path, extras: dict | None = None, forcar_chunks: bool = False) -> tuple[bytes, bytes | None]:
    destino.mkdir(parents=True, exist_ok=True)
    doc = v2_para_documento(v2)
    rend = layout(doc)
    md = rend.markdown.encode("utf-8")
    if extras is not None:  # mesmos bytes que worker.py grava
        aviso = destino / "LEIA-AVISOS.txt"  # mesmo método de gravação do worker (texto, fim de linha da plataforma)
        destino.mkdir(parents=True, exist_ok=True)
        aviso.write_text(review_text(doc), encoding="utf-8")
        extras["LEIA-AVISOS.txt"] = aviso.read_bytes()
        mapa = {"schema_version": 1, "source_sha256": doc.source_sha256, "markdown_file": "document.md",
                "markdown_sha256": sha(md), "source_blocks_file": "document.json", "offset_unit": "unicode_code_points",
                "interval": "half_open", "blocks": rend.refs}
        extras["document-layout.json"] = (json.dumps(mapa, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    o = v2["config"]["options"]
    if forcar_chunks and not o.get("chunks"):
        from traslado.model import Options
        base = Options(language="por")
        o = {"chunks": True, "chunk_size_chars": base.chunk_size_chars, "chunk_overlap_chars": base.chunk_overlap_chars,
             "max_output_bytes": base.max_output_bytes}
    ck = None
    if o.get("chunks"):
        C.write_chunks(doc, rend.markdown, destino / "chunks", size_chars=o["chunk_size_chars"],
                       overlap_chars=o["chunk_overlap_chars"], max_output_bytes=o["max_output_bytes"],
                       rendered=rend)
        ck = (destino / "chunks" / "chunks.jsonl").read_bytes()
    return md, ck


def conferir_render_map(v2: dict, md: str) -> int:
    """Intervalos literais do render_map reproduzem o Markdown a partir de block.text (menos removed)."""
    blocos = {b["id"]: b for p in v2["pages"] for b in p["blocks"]}
    ruins = 0
    for ri in v2["render_map"]:
        if ri["transform"] not in ("literal", "soft_hyphen_join"):
            continue
        texto = blocos[ri["block_id"]]["text"]
        partes, pos = [], ri["source_start"]
        for x in ri.get("removed", []):
            partes.append(texto[pos:x["text_start"]])
            pos = x["text_end"]
        partes.append(texto[pos:ri["source_end"]])
        ruins += md[ri["char_start"]:ri["char_end"]] != "".join(partes)
    return ruins


def conferir_chunks(md: bytes, ck: bytes) -> dict:
    texto = md.decode("utf-8")
    linhas = [json.loads(x) for x in ck.decode("utf-8").splitlines()]
    ruins = 0
    for k, c in enumerate(linhas):
        ok = (c["index"] == k + 1 and type(c["index"]) is int and texto[c["char_start"]:c["char_end"]] == c["text"]
              and c["chunk_id"] == sha(f"{c['source_sha256']}:{c['markdown_sha256']}:{c['char_start']}:{c['char_end']}".encode())
              and c["markdown_sha256"] == sha(md))
        ruins += not ok
    return {"chunks": len(linhas), "invariantes_falhas": ruins}


def validar(v2: dict) -> list[str]:
    est = V.estrutura(v2)
    if est is None:  # sem jsonschema: só invariantes; a estrutura é conferida por validar_modelo_v2.py no venv de contratos
        est = []
    inv = V.invariantes(v2) if not est else []
    return [f"{c}: {d}" for c, d in est + inv]


def dims_pdf(caminho: Path) -> dict[int, tuple[float, float, int]]:
    import fitz
    with fitz.open(caminho) as d:
        return {i + 1: (float(p.rect.width), float(p.rect.height), int(p.rotation)) for i, p in enumerate(d)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", choices=("saida", "exemplo"))
    ap.add_argument("alvo", type=Path)
    ap.add_argument("--pdf", type=Path)
    ap.add_argument("--relatorio", type=Path, required=True)
    ap.add_argument("--v2", type=Path)
    a = ap.parse_args(argv)
    rel: dict = {"modo": a.modo}
    with tempfile.TemporaryDirectory() as tmp:
        if a.modo == "saida":
            d1 = json.loads((a.alvo / "document.json").read_text(encoding="utf-8"))
            md0 = (a.alvo / "document.md").read_bytes()
            ck_arq = a.alvo / "chunks" / "chunks.jsonl"
            ck0 = ck_arq.read_bytes() if ck_arq.exists() else None
            v2, perdas = v1_para_v2(d1, md0.decode("utf-8"), dims_pdf(a.pdf) if a.pdf else None)
            if a.v2:
                a.v2.write_bytes((json.dumps(v2, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            erros = validar(v2)
            extras: dict = {}
            md1, ck1 = projetar(json.loads(json.dumps(v2)), Path(tmp), extras)
            outros = {n: {"original": sha((a.alvo / n).read_bytes()), "projetado": sha(b),
                          "identico": (a.alvo / n).read_bytes() == b}
                      for n, b in extras.items() if (a.alvo / n).exists()}
            rel.update({"fonte_sha256": d1["source_sha256"], "paginas": len(v2["pages"]),
                        "blocos": sum(len(p["blocks"]) for p in v2["pages"]),
                        "render_map": len(v2["render_map"]),
                        "transformacoes": {t: sum(r["transform"] == t for r in v2["render_map"])
                                           for t in sorted({r["transform"] for r in v2["render_map"]})},
                        "v2_sha256": sha(canon(v2)), "v2_valido": not erros, "v2_erros": erros[:20],
                        "render_map_divergencias": conferir_render_map(v2, md0.decode("utf-8")),
                        "perdas_document_json": perdas,
                        "md_sha256_original": sha(md0), "md_sha256_projetado": sha(md1), "md_identico": md0 == md1,
                        "chunks_sha256_original": sha(ck0) if ck0 else None,
                        "chunks_sha256_projetado": sha(ck1) if ck1 else None,
                        "chunks_identico": ck0 == ck1, "outros_artefatos": outros})
            ok = ((not erros) and md0 == md1 and ck0 == ck1 and not perdas.get("render_inexplicavel")
                  and rel["render_map_divergencias"] == 0 and all(x["identico"] for x in outros.values()))
        else:
            v2 = json.loads(a.alvo.read_text(encoding="utf-8"))
            erros = validar(v2)
            md1, ck1 = projetar(v2, Path(tmp), forcar_chunks=True)
            md2, ck2 = projetar(v2, Path(tmp) / "b", forcar_chunks=True)
            rel.update({"v2_valido": not erros, "v2_erros": erros[:20], "md_sha256": sha(md1),
                        "chunks_sha256": sha(ck1) if ck1 else None, "deterministico": md1 == md2 and ck1 == ck2,
                        **(conferir_chunks(md1, ck1) if ck1 else {})})
            ok = (not erros) and md1 == md2 and ck1 == ck2 and not rel.get("invariantes_falhas")
    rel["resultado"] = "ok" if ok else "falha"
    a.relatorio.write_bytes((json.dumps(rel, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({k: rel[k] for k in rel if k not in ("v2_erros",)}, ensure_ascii=False))
    for e in rel.get("v2_erros", []):
        print("erro:", e)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
