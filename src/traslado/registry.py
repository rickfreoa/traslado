# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Single inventory of built-in converters; probing never loads their libraries."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from typing import Callable

from . import ocr
from .model import Document, Options


@dataclass(frozen=True)
class Status:
    state: str
    code: str
    detail: str


@dataclass(frozen=True)
class Converter:
    id: str
    extensions: tuple[str, ...]
    capabilities: tuple[str, ...]
    limits: tuple[str, ...]
    known_losses: tuple[str, ...]
    requires: tuple[str, ...]
    optional: tuple[str, ...]
    extract: Callable[[Path, Path, Options, Document], None]

    def status(self, tesseract: str | None = None) -> Status:
        for module in self.requires:
            if importlib.util.find_spec(module) is None:
                return Status("unavailable", "missing_module:" + module,
                              "Módulo obrigatório ausente: " + module)
        for dependency in self.optional:
            if dependency == "tesseract":
                try:
                    available = ocr.find_tesseract(tesseract)
                except (OSError, ValueError):
                    available = None
                if not available:
                    return Status("degraded", "missing_engine:tesseract",
                                  "Tesseract indisponível; OCR não disponível")
            elif importlib.util.find_spec(dependency) is None:
                return Status("degraded", "missing_module:" + dependency,
                              "Módulo opcional ausente: " + dependency)
        return Status("available", "ok", "Conversor disponível")


def _extractor(name: str):
    # Delay importing formats to avoid a cycle and keep discovery lightweight.
    def extract(path, workspace, options, document):
        from . import formats
        getattr(formats, "_extract_" + name)(path, workspace, options, document)
    return extract


def _legacy(path, workspace, options, document):
    from .legacy import extract
    extract(path, workspace, options, document)


REGISTRY: tuple[Converter, ...] = (
    Converter("legacy", (".doc", ".rtf", ".odt", ".ods", ".odp", ".ppt", ".epub"),
              ("native_text", "tables", "images", "notes"),
              ("max_input_bytes", "max_archive_bytes", "max_cells", "max_output_bytes"),
              ("legacy_engine_experimental", "legacy_layout_unavailable", "image_semantics_unverified",
               "legacy_table_merged_cells_need_review", "legacy_note_definition_unreferenced", "legacy_image_unavailable"),
              ("anydoc",), (), _legacy),
    Converter("pdf", (".pdf",), ("native_text", "tables", "images", "ocr"),
              ("max_pages", "max_pixels", "max_cells", "dpi", "ocr_timeout", "max_output_bytes"),
              ("pdf_form_field_semantics_unverified", "pdf_annotations_not_extracted",
               "native_unicode_suspect_retained_in_json", "native_unicode_suspect",
               "pdf_table_overlap_text_preserved_possible_duplication",
               "pdf_overlapping_table_regions_need_review", "pdf_table_cell_text_mismatch_preserved",
               "pdf_table_merged_cells_need_review", "reading_order_two_column_heuristic",
               "image_semantics_unverified", "ocr_low_confidence"),
              ("fitz",), ("tesseract",), _extractor("pdf")),
    Converter("docx", (".docx",), ("native_text", "tables", "images"),
              ("max_archive_bytes", "max_cells", "max_pixels"),
              ("docx_tracked_changes_not_resolved", "docx_nested_table_structure_flattened",
               "docx_vmerge_continuation_text_preserved", "docx_vmerge_inconsistent_content_preserved",
               "docx_headers_footers_not_extracted", "docx_comments_not_extracted",
               "docx_note_definition_unreferenced", "docx_note_reference_unresolved",
               "docx_note_complex_content_flattened", "docx_note_duplicate_or_missing_id", "docx_formatting_partial",
               "image_semantics_unverified"), ("docx",), ("PIL",), _extractor("docx")),
    Converter("xlsx", (".xlsx",), ("native_text", "tables"),
              ("max_archive_bytes", "max_pages", "max_cells"),
              ("xlsx_formulas_preserved_not_calculated", "xlsx_merges_styles_images_not_extracted"),
              ("openpyxl",), (), _extractor("xlsx")),
    Converter("pptx", (".pptx",), ("native_text", "tables", "images"),
              ("max_archive_bytes", "max_pages", "max_cells", "max_pixels"),
              ("pptx_groups_notes_charts_not_extracted", "pptx_order_geometric_heuristic",
               "pptx_shape_coordinates_missing_document_order", "image_semantics_unverified"),
              ("pptx",), ("PIL",), _extractor("pptx")),
    Converter("image", (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"),
              ("images", "ocr"), ("max_pages", "max_pixels", "ocr_timeout", "max_output_bytes"),
              ("image_text_not_extracted_native_mode", "image_semantics_unverified", "ocr_low_confidence"),
              ("PIL",), ("tesseract",), _extractor("image")),
    Converter("csv", (".csv", ".tsv"), ("native_text", "tables"), ("max_cells",),
              ("unicode_suspect",), (), (), _extractor("csv")),
    Converter("html", (".html", ".htm"), ("native_text",), (),
              ("html_table_structure_not_preserved", "unicode_suspect"), (), (), _extractor("html")),
    Converter("text", (".txt", ".md", ".json", ".xml"), ("native_text",), (),
              ("unicode_suspect",), (), (), _extractor("text")),
)

_BY_EXTENSION = {extension: converter for converter in REGISTRY for extension in converter.extensions}
if len(_BY_EXTENSION) != sum(len(converter.extensions) for converter in REGISTRY):
    raise ValueError("Extensão cadastrada em mais de um conversor")
SUPPORTED = frozenset(_BY_EXTENSION)
REASON_CODES = frozenset({"ok", "disabled", "missing_engine:tesseract"} | {
    "missing_module:" + module for converter in REGISTRY
    for module in (*converter.requires, *converter.optional) if module != "tesseract"
})


def lookup(suffix: str) -> Converter | None:
    return _BY_EXTENSION.get(suffix.lower())
