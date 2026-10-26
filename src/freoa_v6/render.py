from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from .model import Block, Document


def table_markdown(rows: list[list[str]], *, max_cells: int = 1_000_000) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    # Ragged CSV/XLSX rows expand when padded. Count the rendered grid before
    # allocating it, rather than only the cells present in the input.
    if width * len(rows) > max_cells:
        raise ValueError("Tabela renderizada excede limite de células")
    def cell(s: str) -> str:
        return html.escape(str(s)).replace("|", "\\|").replace("\n", "<br>")
    clean = [[cell(x) for x in row] + [""] * (width - len(row)) for row in rows]
    # A neutral header avoids claiming the first source row is a semantic header.
    out = ["| " + " | ".join(f"Coluna {i+1}" for i in range(width)) + " |",
           "| " + " | ".join("---" for _ in range(width)) + " |"]
    out.extend("| " + " | ".join(row) + " |" for row in clean)
    return "\n".join(out)


@dataclass
class MarkdownLayout:
    """The human-readable document and its exact source-block spans.

    Provenance lives beside the Markdown, rather than as comments interrupting
    each paragraph. Spans use half-open Unicode code-point offsets, just like
    the chunks. The extraction model is never modified by presentation.
    """
    markdown: str
    refs: list[dict[str, Any]]
    atomic: list[tuple[int, int]]
    review_notice: tuple[int, int] | None = None


_REFLOW_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
_STRUCTURE = re.compile(r"^(?:\s{2,}|\s*(?:#{1,6}\s|>|[-+*]\s|(?:\d+|[A-Za-z]|[ivxlcdmIVXLCDM]+)[.)]\s|`|~|<|\||!\[))")


def _readable_text(block: Block, source_name: str) -> tuple[str, str]:
    """Join only likely physical wraps in known PDF/OCR prose.

    Blank lines and block/page boundaries remain paragraph boundaries. Short
    lines, lists, indentation, Markdown, and ordinary hyphens remain literal.
    This is deliberately not semantic paragraph or heading reconstruction.
    """
    text = block.text
    if (block.kind != "text" or block.engine not in {"pymupdf", "tesseract"}
            or PurePath(source_name).suffix.lower() not in _REFLOW_SUFFIXES):
        return text, "literal"
    result: list[str] = []
    changed = False
    # Do not let line joining alter a fence, table, list or HTML fragment.
    # A paragraph containing such a line is preserved as a whole.
    for paragraph in re.split(r"(\n[ \t]*\n)", text):
        lines = paragraph.split("\n")
        if "`" in paragraph or any(_STRUCTURE.match(line) or line.isupper() or line.endswith("  ")
                                    or re.search(r"\S[ \t]{2,}\S", line) for line in lines):
            result.append(paragraph)
            continue
        if not lines:
            result.append(paragraph)
            continue
        rendered = lines[0]
        for previous, following in zip(lines, lines[1:]):
            left, right = previous.rstrip(), following.lstrip()
            # Explicit discretionary hyphens carry stronger evidence than an
            # ordinary '-' which may be part of the author's lexical spelling.
            soft = bool(left.endswith("\u00ad") and len(left) > 1
                        and left[-2].isalpha() and right and right[0].isalpha())
            continuation = bool(len(left) >= 40 and len(left.split()) >= 6
                                and right and (right[0].islower()
                                    or (right[0].isupper() and len(right) >= 40 and len(right.split()) >= 6))
                                and len(right.split()) >= 2
                                and (left[-1:].isalnum() or left[-1:] in {",", ";", ":"}))
            if soft:
                rendered = rendered.rstrip()[:-1] + right
                changed = True
            elif continuation:
                rendered = rendered.rstrip() + " " + right
                changed = True
            else:
                rendered += "\n" + following
        result.append(rendered)
    return "".join(result), "conservative_pdf_ocr_line_join" if changed else "literal"


def _native_continuation(previous: Block | None, current: Block, source_name: str) -> bool:
    """Only join native fragments with matching column and nearby geometry.

    This does not remove running headers/footers or infer across pages, hyphens,
    OCR, or columns. Native extraction can split one paragraph into two blocks.
    """
    if (previous is None or PurePath(source_name).suffix.lower() != ".pdf"
            or previous.kind != "text" or current.kind != "text"
            or previous.engine != "pymupdf" or current.engine != "pymupdf"):
        return False
    left, right = previous.text.rstrip(), current.text.lstrip()
    if (len(left) < 100 or len(left.split()) < 12 or not left
            or not (left[-1].isalnum() or left[-1] in {",", ";", ":"})
            or not right or not right[0].islower()
            or len(right.split()) < 4 or len(right.split("\n", 1)[0]) < 25):
        return False
    if "`" in left or "`" in right or any(_STRUCTURE.match(line) or line.isupper() or line.endswith("  ")
           or re.search(r"\S[ \t]{2,}\S", line) for line in (left + "\n" + right).split("\n")):
        return False
    boxes = (previous.bbox, current.bbox)
    if any(not box or len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v)
                                              for v in box) for box in boxes):
        return False
    a, b = boxes
    aw, bw = a[2] - a[0], b[2] - b[0]
    if aw <= 0 or bw <= 0 or a[3] <= a[1] or b[3] <= b[1]:
        return False
    return (abs(a[0] - b[0]) <= 6 and abs(aw - bw) <= max(20, .2 * max(aw, bw))
            and b[1] >= a[1] and -3 <= b[1] - a[3] <= 14)


def layout(document: Document) -> MarkdownLayout:
    parts = ["# " + document.source_name.replace("\n", " ")]
    separators = [""]
    refs: list[dict[str, Any]] = []
    atomic_parts: list[int] = []
    review_part = None
    if document.status == "partial" or document.warnings or any(p.warnings for p in document.pages):
        review_part = len(parts)
        parts.append("> Este resultado requer revisão. Consulte [os avisos de conversão](LEIA-AVISOS.txt).")
        separators.append("\n\n")
        atomic_parts.append(review_part)
    for page in document.pages:
        previous = None
        for index, block in enumerate(page.blocks, 1):
            presentation = "literal"
            if block.kind == "table":
                text = table_markdown(block.rows or [], max_cells=document.options.get("max_cells", 1_000_000))
            elif block.kind == "html_table":
                text = block.text
            elif block.kind == "image":
                if not block.asset or not re.fullmatch(r"assets/[a-zA-Z0-9_.-]+", block.asset):
                    previous = None
                    continue
                text = f"![Imagem da página {page.number}]({block.asset})"
            else:
                text, presentation = _readable_text(block, document.source_name)
            part = len(parts)
            parts.append(text)
            joined = _native_continuation(previous, block, document.source_name)
            separators.append(" " if joined else "\n\n")
            if joined:
                presentation += "+native_geometry_continuation"
            refs.append({"part": part, "page": page.number, "block": index,
                         "kind": block.kind, "engine": block.engine,
                         "route": page.route, "warnings": list(page.warnings),
                         "confidence": block.confidence, "bbox": block.bbox,
                         "asset": block.asset, "presentation": presentation})
            if getattr(block, "role", None) is not None:
                refs[-1]["role"] = block.role
            if block.kind in {"table", "html_table", "image"}:
                atomic_parts.append(part)
            previous = block
    markdown = "".join(separator + part for separator, part in zip(separators, parts)).rstrip() + "\n"
    starts, cursor = [], 0
    for separator, part in zip(separators, parts):
        cursor += len(separator)
        starts.append(cursor)
        cursor += len(part)
    for ref in refs:
        part = ref.pop("part")
        ref["char_start"] = min(starts[part], len(markdown) - 1)
        ref["char_end"] = min(starts[part] + len(parts[part]), len(markdown) - 1)
    atomic = [(starts[p], min(starts[p] + len(parts[p]), len(markdown) - 1))
              for p in atomic_parts]
    review_notice = ((starts[review_part], starts[review_part] + len(parts[review_part]))
                     if review_part is not None else None)
    return MarkdownLayout(markdown, refs, atomic, review_notice)


def render(document: Document) -> str:
    return layout(document).markdown
