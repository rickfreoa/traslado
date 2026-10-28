# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys
import time

from .errors import ConversionError, classify, partial_error

from .formats import extract
from .model import Document, Options
from .render import layout
from .review import review_text
from .safety import is_link, sha256


def _content_layout(document, suffix):
    """Use the detected format for presentation, retaining the source title.

    The renderer uses source_name's suffix to enable PDF/OCR line joining.
    Keep that existing policy for renamed inputs without altering provenance.
    """
    if Path(document.source_name).suffix.lower() == suffix:
        return layout(document)
    name = str(Path(document.source_name).with_suffix(suffix))
    rendered = layout(replace(document, source_name=name))
    old = "# " + name.replace("\n", " ")
    new = "# " + document.source_name.replace("\n", " ")
    delta = len(new) - len(old)
    rendered.markdown = new + rendered.markdown[len(old):]
    for ref in rendered.refs:
        ref["char_start"] += delta
        ref["char_end"] += delta
    rendered.atomic = [(start + delta, end + delta) for start, end in rendered.atomic]
    if rendered.review_notice is not None:
        rendered.review_notice = tuple(offset + delta for offset in rendered.review_notice)
    return rendered


def main() -> int:
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    workspace = Path(request["workspace"])
    options = Options(**request["options"])
    document = Document(request["source_name"], request["sha256"], options=request["options"])
    start = time.monotonic()
    try:
        snapshot = Path(request["snapshot"])
        if is_link(snapshot):
            raise ConversionError("io", "Snapshot não corresponde ao hash de origem")
        if snapshot.stat().st_size > options.max_input_bytes:
            raise ConversionError("resource_limit", "Snapshot não corresponde ao hash de origem")
        if sha256(snapshot) != request["sha256"]:
            raise ConversionError("io", "Snapshot não corresponde ao hash de origem")
        document.warnings.extend(request.get("warnings", []))
        extract(snapshot, workspace, options, document)
        if options.advanced_kit:
            if Path(request["snapshot"]).suffix == ".pdf":
                from .advanced import refine
                refine(Path(request["snapshot"]), workspace, options, document)
            else:
                document.warnings.append("advanced_pdf_only")
        if sha256(snapshot) != request["sha256"]:
            raise ConversionError("io", "Snapshot mudou durante a leitura")
        if any(p.warnings for p in document.pages) or document.warnings:
            document.status = "partial"
        document.duration_seconds = time.monotonic() - start
        effective_layout = (lambda doc: _content_layout(doc, snapshot.suffix)) if request.get("warnings") else layout
        rendered = effective_layout(document)
        data = document.to_dict()
        data["fidelity"] = "unverified"
        data["presentation"] = {"markdown": "document.md", "layout": "document-layout.json",
                                "raw_blocks": "pages[].blocks[]", "policy": "conservative_pdf_ocr_reflow"}
        (workspace / "document.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown = rendered.markdown
        # Canonical LF bytes: chunk hashes/offsets refer to this physical file,
        # never to newline-normalized read_text() content on Windows.
        (workspace / "document.md").write_bytes(markdown.encode("utf-8"))
        mapping = {"schema_version": 1, "source_sha256": document.source_sha256,
                   "markdown_file": "document.md", "markdown_sha256": sha256(workspace / "document.md"),
                   "source_blocks_file": "document.json", "offset_unit": "unicode_code_points",
                   "interval": "half_open", "blocks": rendered.refs}
        (workspace / "document-layout.json").write_bytes(
            (json.dumps(mapping, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        review = review_text(document)
        if request.get("warnings"):
            review += "\n" + "\n".join(request["warnings"]) + "\n"
        (workspace / "LEIA-AVISOS.txt").write_text(review, encoding="utf-8")
        if options.chunks:
            from . import chunks
            remaining = options.max_output_bytes - sum(p.stat().st_size for p in workspace.rglob("*") if p.is_file())
            if remaining < 1:
                raise ConversionError('resource_limit', "Conversão excede limite de saída antes dos chunks")
            # One isolated worker per document. Reuse the same presentation
            # adapter for the chunk module's exact-Markdown/span validation.
            previous_layout = chunks.layout
            try:
                chunks.layout = effective_layout
                chunks.write_chunks(document, markdown, workspace / "chunks",
                                    size_chars=options.chunk_size_chars, overlap_chars=options.chunk_overlap_chars,
                                    max_output_bytes=remaining)
            finally:
                chunks.layout = previous_layout
        (workspace / "progress.json").unlink(missing_ok=True)
        (workspace / ".progress.json.tmp").unlink(missing_ok=True)
        total = sum(p.stat().st_size for p in workspace.rglob("*") if p.is_file())
        if total > options.max_output_bytes:
            raise ConversionError('resource_limit', "Conversão excede limite de saída")
        if document.status == "partial":
            issue = partial_error(document)
            (Path(sys.argv[1]).parent / "partial-error.json").write_text(
                json.dumps({"code": issue.code, "data": issue.data or {}}), encoding="utf-8")
        return 0
    except Exception as exc:
        issue = classify(exc)
        (workspace / "error.json").write_text(json.dumps({"type": type(exc).__name__, "error": str(exc)[:2000], "code": issue.code, "data": issue.data or {}},
                                                       ensure_ascii=False), encoding="utf-8")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
