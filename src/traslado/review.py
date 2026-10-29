# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Readable audit summaries. OCR confidence is not a fidelity certificate."""
from collections import defaultdict
from .model import Document

LABELS = {
    "legacy_engine_experimental": "Motor legado experimental — legacy_engine_experimental",
    "legacy_layout_unavailable": "Diagramação original indisponível — legacy_layout_unavailable",
    "legacy_table_merged_cells_need_review": "Células mescladas exigem revisão — legacy_table_merged_cells_need_review",
    "legacy_note_definition_unreferenced": "Nota sem chamada no texto — legacy_note_definition_unreferenced",
    "legacy_image_unavailable": "Imagem indisponível — legacy_image_unavailable",
    "format_extension_mismatch": "Extensão divergente do conteúdo — format_extension_mismatch",
    "ocr_failed": "OCR falhou nesta página; revise o original e o texto recuperado",
    "region_ocr_failed": "OCR de uma região falhou; revise a imagem original",
    "ocr_empty": "OCR não encontrou texto; pode ser página ilustrada, vazia ou ilegível",
    "ocr_low_confidence": "OCR com baixa confiança; confira nomes, números e pontuação",
    "native_text_retained_in_json_ocr_supersedes_markdown": "OCR substituiu o texto nativo no Markdown; o texto anterior consta no JSON",
    "image_semantics_unverified": "Imagem incluída sem interpretação de seu conteúdo",
    "scan_preview_failed": "Prévia da página não pôde ser gerada",
    "page_render_failed": "Não foi possível renderizar esta página para OCR",
}


def warning_label(code: str) -> str:
    key = code.split(":", 1)[0]
    label = LABELS.get(key, key.replace("_", " "))
    if key == "format_extension_mismatch" and ":" in code:
        label += ":" + code.split(":", 1)[1]
    return label


def review_text(document: Document) -> str:
    groups = defaultdict(list)
    for page in document.pages:
        for warning in page.warnings:
            groups[warning.split(":", 1)[0]].append(page.number)
    lines = [f"Arquivo: {document.source_name}", f"SHA-256: {document.source_sha256}",
             f"Resultado: {document.status}",
             "Fidelidade não verificada. Confira o PDF original para citações e dados decisivos.", ""]
    omitted = [p.number for p in document.pages if p.metrics.get("scan_page_image_in_source_pdf")
               and not p.metrics.get("scan_previews_exported")]
    if omitted:
        lines += [f"Imagens de página inteira mantidas somente no PDF original: {len(omitted)} páginas.",
                  "Podem incluir ilustrações. Para exportar prévias reduzidas, marque Incluir prévias de páginas ou use scan_previews=true.",
                  "Páginas: " + ", ".join(map(str, omitted)), ""]
    for code, pages in sorted(groups.items()):
        numbers = sorted(set(pages))
        lines.append(f"{warning_label(code)} ({len(numbers)} páginas): " + ", ".join(map(str, numbers)))
    for warning in document.warnings:
        lines.append("Documento: " + warning_label(warning))
    if not groups and not document.warnings:
        lines.append("Nenhum aviso de extração. Isso não comprova a precisão do texto.")
    lines += ["", "Detalhes por página, motores e confiança: document.json."]
    return "\n".join(lines) + "\n"
