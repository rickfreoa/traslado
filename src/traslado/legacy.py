# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Experimental local anydoc model adapter. No hosted/OCR entry point is used."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import importlib.machinery
from pathlib import Path
import re
import sys
import zipfile

from .errors import ConversionError
from .model import Block, Document, Options, Page
from .render import table_markdown
from .safety import check_archive


def extract(path: Path, workspace: Path, options: Options, document: Document) -> None:
    if not options.legacy:
        raise ConversionError("unsupported", "Formato legado; use --engine legacy")
    try:
        # Load only the compiled extension. Even the package's urllib imports
        # are unnecessary here; no hosted Python wrapper is executed.
        native = sys.modules.get("anydoc._anydoc")
        if native is None:
            package = importlib.util.find_spec("anydoc")
            if package is None:
                raise ImportError("anydoc")
            directory = Path(package.origin).parent
            binary = next((directory / ("_anydoc" + suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES
                           if (directory / ("_anydoc" + suffix)).is_file()), None)
            if binary is None:
                raise ImportError("anydoc._anydoc")
            spec = importlib.util.spec_from_file_location("anydoc._anydoc", binary)
            native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(native)
            sys.modules["anydoc._anydoc"] = native
    except ImportError as exc:
        raise ConversionError("missing_part", "Instale pip install traslado[legacy]") from exc
    try:
        version = importlib.metadata.version("firecrawl-anydoc")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ConversionError("missing_part", "Instale pip install traslado[legacy]") from exc
    if version != "0.2.4":
        raise ConversionError("missing_part", "Use pip install traslado[legacy] (anydoc 0.2.4)")
    if path.stat().st_size > options.max_input_bytes:
        raise ConversionError("resource_limit", "Entrada excede limite de bytes")
    if zipfile.is_zipfile(path):
        try:
            check_archive(path, options)
            # The native parser does not consistently distinguish unsupported
            # compression and encryption. Validate member headers with zipfile
            # in the worker, without decompressing their contents.
            with zipfile.ZipFile(path) as archive:
                for info in archive.infolist():
                    with archive.open(info):
                        pass
        except NotImplementedError as exc:
            raise ConversionError("unsupported", str(exc)) from exc
        except RuntimeError as exc:
            if "password" in str(exc).lower() or "encrypted" in str(exc).lower():
                raise ConversionError("encrypted", str(exc)) from exc
            raise
    try:
        source = native.to_document(path.read_bytes(), format=path.suffix.lstrip("."))
    except native.ConvertError as exc:
        codes = ((native.UnsupportedError, "unsupported"), (native.MalformedError, "malformed"),
                 (native.EncryptedError, "encrypted"), (native.ResourceLimitError, "resource_limit"),
                 (native.MissingPartError, "missing_part"), (native.NeedsOcrError, "needs_ocr"))
        code = next((code for kind, code in codes if isinstance(exc, kind)), "malformed")
        data = {"error_pages": list(exc.pages)} if code == "needs_ocr" else None
        raise ConversionError(code, str(exc), data) from exc
    except OSError as exc:
        raise ConversionError("io", str(exc)) from exc
    _map(source, workspace, options, document)


def _map(source, workspace: Path, options: Options, document: Document) -> None:
    from .formats import _note_label

    document.submotor = "anydoc"
    document.submotor_version = "0.2.4"
    document.experimental = True
    document.warnings.extend(["legacy_engine_experimental", "legacy_layout_unavailable"])
    page = Page(1, "legacy")
    document.pages.append(page)
    notes = {}
    for note in source.notes:
        if note.id in notes:
            raise ConversionError("malformed", "Identificador de nota duplicado")
        notes[note.id] = note
    labels, called = {}, []
    assets = {asset.id: asset for asset in source.assets}
    written = {}
    cells = 0
    budget = 0

    def charge(text="", depth=0):
        nonlocal budget
        budget += len(text.encode("utf-8")) + 1
        if depth > 100 or budget > options.max_output_bytes:
            raise ConversionError("resource_limit", "Modelo legado excede limite de saída ou profundidade")

    def warn(text):
        if text not in page.warnings:
            page.warnings.append(text)

    def label(key):
        if key not in notes:
            raise ConversionError("missing_part", "Definição de nota ausente: " + str(key))
        if key not in labels:
            labels[key] = _note_label(notes[key].kind, str(len(labels) + 1))
            called.append(key)
        return labels[key]

    def inline(items, depth=0):
        nonlocal budget
        charge(depth=depth)
        out = []
        for item in items or []:
            kind = item.kind
            if kind == "text":
                text = item.text or ""
            elif kind == "note_ref":
                text = label(item.note_id)
            elif kind == "link":
                target = item.target
                url = ("#" if target.kind == "anchor" else "") + target.value
                # Angle brackets preserve spaces and Markdown parentheses.
                url = url.replace("<", "%3C").replace(">", "%3E").replace("\n", "%0A")
                text = "[" + inline(item.content, depth + 1) + "](<" + url + ">)"
            elif kind == "image":
                text = item.alt or ""
                image = item.source
                if options.images and image and image.kind == "asset" and image.asset_id in assets:
                    key = image.asset_id
                    if key not in written:
                        asset = assets[key]
                        budget += len(asset.data)
                        if budget > options.max_output_bytes:
                            raise ConversionError("resource_limit", "Ativo legado excede limite")
                        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                                  "image/tiff": ".tif", "image/bmp": ".bmp", "image/webp": ".webp",
                                  "image/svg+xml": ".svg"}.get(asset.media_type, ".bin")
                        target = workspace / "assets" / f"legacy-{len(written)+1}{suffix}"
                        target.parent.mkdir(exist_ok=True)
                        # Preserve bytes without decoding or requiring Pillow:
                        # Core + legacy must also retain embedded images.
                        target.write_bytes(asset.data)
                        written[key] = "assets/" + target.name
                        page.metrics.setdefault("legacy_assets", []).append({
                            "asset": written[key], "source_asset_id": key,
                            "media_type": asset.media_type, "origin_part": asset.origin_part})
                    text = "![" + text.replace("]", "\\]") + "](" + written[key] + ")"
                    warn("image_semantics_unverified")
                elif options.images:
                    warn("legacy_image_unavailable")
            elif kind == "line_break":
                text = "\n"
            elif kind == "math":
                text = "$" + (item.text or "") + "$"
            elif kind == "checkbox":
                text = "[x] " if item.checked else "[ ] "
            elif kind == "anchor":
                import html
                text = '<a id="' + html.escape(item.anchor or "", quote=True) + '"></a>'
            else:
                raise ConversionError("unsupported", "Inline anydoc desconhecido: " + kind)
            charge(text, depth)
            out.append(text)
        return "".join(out)

    def blocks(items, depth=0):
        nonlocal cells
        charge(depth=depth)
        out = []
        for item in items:
            kind = item.kind
            details = {"legacy_kind": kind}
            if kind in {"paragraph", "heading"}:
                text = inline(item.content, depth + 1)
                if kind == "heading":
                    text = "#" * max(1, min(6, item.level or 1)) + " " + text
            elif kind == "list":
                for index, entry in enumerate(item.list.items):
                    parts = blocks(entry.blocks, depth + 1)
                    text = "\n".join(block_text(b) for b in parts)
                    marker = entry.marker_label or ("-" if item.list.marker == "bullet" else str(item.list.start + index) + ".")
                    out.append(Block("text", marker + " " + text.replace("\n", "\n  "), engine="anydoc",
                                     details={"legacy_kind": "list_item", "marker_family": item.list.marker}))
                continue
            elif kind == "table":
                grid = item.table.grid
                cells += len(grid) * max((len(row) for row in grid), default=0)
                if cells > options.max_cells:
                    raise ConversionError("resource_limit", "Tabela legada excede limite de células")
                rows = []
                for row in grid:
                    values = []
                    for slot in row:
                        if slot.kind == "covered":
                            values.append("")
                            warn("legacy_table_merged_cells_need_review")
                        else:
                            cell = slot.cell
                            if cell.col_span > 1 or cell.row_span > 1:
                                warn("legacy_table_merged_cells_need_review")
                            values.append("\n".join(block_text(b) for b in blocks(cell.blocks, depth + 1)))
                    rows.append(values)
                out.append(Block("table", rows=rows, engine="anydoc", details={**details,
                                 "header_rows": item.table.header_rows, "table_kind": item.table.kind}))
                continue
            elif kind == "block_quote":
                text = "\n\n".join(block_text(b) for b in blocks(item.blocks or [], depth + 1))
                text = "> " + text.replace("\n", "\n> ")
            elif kind == "code_block":
                text = item.text or ""
                fence = "`" * max(3, max((len(run) for run in re.findall(r'`+', text)), default=0) + 1)
                text = fence + (item.lang or "").replace("\n", "") + "\n" + text + "\n" + fence
            elif kind == "rule":
                text = "---"
            elif kind == "math":
                text = "$$\n" + (item.text or "") + "\n$$"
            else:
                raise ConversionError("unsupported", "Bloco anydoc desconhecido: " + kind)
            charge(text, depth)
            out.append(Block("text", text, engine="anydoc", details=details))
        return out

    def block_text(block):
        return table_markdown(block.rows, max_cells=options.max_cells) if block.kind == "table" else block.text

    page.blocks.extend(blocks(source.blocks))
    for key in notes:
        if key not in labels:
            warn("legacy_note_definition_unreferenced")
            label(key)
    # A growing call list also accommodates references inside note definitions.
    for key in called:
        note = notes[key]
        text = "\n\n".join(block_text(b) for b in blocks(note.blocks)).lstrip()
        page.blocks.append(Block("text", labels[key] + ": " + text.replace("\n", "\n    "), engine="anydoc",
                                 details={"note_family": note.kind, "note_id": key}))
