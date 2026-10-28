# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Deterministic character-based RAG chunks with source provenance.

Offsets are half-open Unicode code-point offsets in the exact document.md,
not byte offsets or token estimates. Tables and fenced code remain whole.
"""
from __future__ import annotations

import hashlib
import bisect
import json
from pathlib import Path
import re
from typing import Any, Iterator

from .model import Document
from .render import layout


def _layout(document: Document, markdown: str) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    # The renderer owns both presentation and spans. Never discover source
    # provenance by parsing marker-looking text supplied by the input document.
    effective = layout(document)
    if effective.markdown != markdown:
        raise ValueError("Chunks exigem o Markdown exato produzido por render(document)")
    return effective.refs, effective.atomic


def _block_ends(blocks: list[tuple[int, int]] | None):
    """Return end_of(position): end of the first source block holding `position`, or -1.

    The renderer owns the block spans, so this boundary is provable and never depends on
    the content (inside code, a leading `#` is content, not a heading). Lookup is O(log n).
    """
    ordered = sorted(blocks or [])
    starts = [begin for begin, _ in ordered]
    reach, top = [], -1
    for _, end in ordered:
        top = max(top, end)
        reach.append(top)

    def end_of(position: int) -> int:
        k = bisect.bisect_right(reach, position)  # first block whose running end passes position
        return ordered[k][1] if k < len(ordered) and starts[k] <= position else -1

    return end_of


def _protected(markdown: str, atomic: list[tuple[int, int]], *, code_only: bool = False,
               blocks: list[tuple[int, int]] | None = None,
               unclosed: list[dict[str, Any]] | None = None,
               confined: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """Protect native Markdown tables, HTML tables, fences and generated markers.

    A fence or HTML table that no later line closes used to protect everything up to the end of
    the document. Now it protects only up to the end of the source block that opened it (the
    renderer's `blocks`). Whether a construct closes is decided by lookahead tables built in one
    backward pass, so the scan stays a single linear pass and pairing of constructs that do close,
    even in a later block, is exactly the old one. Each unclosed case is appended to `unclosed`
    as {"kind", "start", "end"}, so callers can warn. Without `blocks` the old behaviour is kept.

    `confined` lists blocks of extracted PDF/OCR prose: the converter never authors a fence there,
    so a fence-shaped line (OCR noise such as `~~~`) pairs only with a closer inside the same block;
    otherwise it is sealed at that block's end, like an unclosed fence.
    """
    end_of = _block_ends(blocks)
    confined_end = _block_ends(confined)
    lines = markdown.splitlines(keepends=True)
    total = len(lines)
    starts, cursor = [], 0
    for line in lines:
        starts.append(cursor)
        cursor += len(line)
    matches = [re.match(r"^ {0,3}(`{3,}|~{3,})", line) for line in lines]
    # longest closing-shaped fence line (only the fence character) after line i, per character
    closer = {"`": [0] * (total + 1), "~": [0] * (total + 1)}
    for i in range(total - 1, -1, -1):
        for row in closer.values():
            row[i] = row[i + 1]
        match = matches[i]
        if match and not lines[i].strip().lstrip(match.group(1)[0]).strip():
            row = closer[match.group(1)[0]]
            row[i] = max(row[i], len(match.group(1)))

    def seal(kind: str, begin: int, end: int | None = None) -> int:
        end = end_of(begin) if end is None else end
        if end <= begin:  # no provable boundary: the old behaviour
            end = len(markdown)
        atomic.append((begin, end))
        if unclosed is not None:
            unclosed.append({"kind": kind, "start": begin, "end": end})
        return end

    # Pass 1: fences. A fence closes at the first later closing line; one that no later line closes
    # is sealed up to the end of its source block. `free` marks lines outside any fence.
    free = [False] * total
    fence: tuple[str, int, int] | None = None
    skip_until = 0
    for i in range(total):
        if starts[i] < skip_until:
            continue
        line = lines[i]
        match = matches[i]
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not line.strip().lstrip(fence[0]).strip():
                atomic.append((fence[2], starts[i] + len(line)))
                fence = None
            continue
        if match:
            char, size = match.group(1)[0], len(match.group(1))
            limit = confined_end(starts[i])
            if limit > starts[i]:
                # Prose block: look for the same closer the loop below would take, but only inside it.
                j = i + 1
                while j < total and starts[j] < limit and not (
                        matches[j] and matches[j].group(1)[0] == char and len(matches[j].group(1)) >= size
                        and not lines[j].strip().lstrip(char).strip()):
                    j += 1
                if j < total and starts[j] < limit:
                    fence = (char, size, starts[i])
                else:
                    skip_until = seal("fence", starts[i], limit)
            elif closer[char][i + 1] >= size:
                fence = (char, size, starts[i])
            else:
                skip_until = seal("fence", starts[i])
            continue
        free[i] = True
    # Pass 2: HTML tables, native tables and markers, on the lines outside fences.
    table_closes = [False] * (total + 1)
    for i in range(total - 1, -1, -1):
        table_closes[i] = table_closes[i + 1] or (free[i] and "</table>" in lines[i].lower())
    table_start: int | None = None
    table_until = 0
    for i in range(total):
        if not free[i]:
            continue
        line = lines[i]
        stripped = line.strip()
        if not code_only and re.match(r"^\s*<!-- (?:sha256:|page:|block:)", line):
            atomic.append((starts[i], starts[i] + len(line)))
        if not code_only and table_start is None and starts[i] >= table_until and "<table" in stripped.lower():
            if table_closes[i]:
                table_start = starts[i]
            else:
                table_until = seal("table", starts[i])
        if table_start is not None and "</table>" in stripped.lower():
            atomic.append((table_start, starts[i] + len(line)))
            table_start = None
        # The delimiter, not pipes alone, identifies a Markdown table.
        if not code_only and i and "|" in line and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", line):
            end = i + 1
            while end < total and "|" in lines[end] and lines[end].strip():
                end += 1
            atomic.append((starts[i - 1], starts[end] if end < total else len(markdown)))
    if unclosed is not None:
        unclosed.sort(key=lambda item: (item["start"], item["kind"]))
    # Inline code also carries literal paths. Keep both delimiters in one chunk.
    # Inline spans cannot cross a block boundary or consume fenced code.
    # Index matching delimiter lengths once, avoiding a quadratic forward scan.
    existing = sorted(atomic)
    cursor = 0
    gaps = []
    for a, b in existing:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < len(markdown):
        gaps.append((cursor, len(markdown)))
    for begin, end in gaps:
        offset = begin
        for paragraph in re.split(r"(\n[ \t]*\n)", markdown[begin:end]):
            ticks = list(re.finditer(r"`+", paragraph))
            next_tick, last = {}, {}
            for i in range(len(ticks) - 1, -1, -1):
                length = len(ticks[i].group())
                if length in last:
                    next_tick[i] = last[length]
                last[length] = i
            index = 0
            while index < len(ticks):
                closing = next_tick.get(index)
                if closing is None:
                    index += 1
                else:
                    atomic.append((offset + ticks[index].start(), offset + ticks[closing].end()))
                    index = closing + 1
            offset += len(paragraph)
    merged: list[tuple[int, int]] = []
    for start, end in sorted(atomic):
        if end <= start:
            continue
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _advanced_images(markdown: str, refs: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """Local images reconstructed by the sanitizer, with source-level spans.

    Match only the constrained syntax emitted inside an advanced source block.
    Look for literal code in the whole document, so a sliced chunk cannot lose
    the opening delimiter and accidentally turn a literal example into a link.
    """
    advanced = [ref for ref in refs if ref["engine"].startswith("mineru-kit-")]
    if not advanced:
        return []
    protected = _protected(markdown, [], code_only=True, blocks=[(ref["char_start"], ref["char_end"]) for ref in refs])
    images = []
    for match in re.finditer(r"!\[Imagem reconstruída\]\(assets/(advanced-[a-zA-Z0-9_.-]+\.png)\)", markdown):
        begin, end = match.span()
        if (any(ref["char_start"] <= begin and end <= ref["char_end"] for ref in advanced)
                and not any(a < end and b > begin for a, b in protected)):
            images.append((begin, end, match.group(1)))
    return images


def _overlap_start(markdown: str, start: int, end: int, overlap: int,
                   atomic: list[tuple[int, int]]) -> tuple[int, str]:
    """Reduce overlap to the next whitespace if its start bisects a word.

    Recheck atoms after word alignment and words after atom alignment. Never
    scan beyond the previous chunk: a long unbroken word may leave zero overlap.
    """
    if not overlap:
        return end, "none"
    next_start = max(start + 1, end - overlap)
    word_adjusted = atom_adjusted = False
    while next_start < end:
        for a, b in atomic:
            if a < next_start < b:
                next_start = b
                atom_adjusted = True
                break
        if next_start >= end:
            break
        if markdown[next_start].isspace():
            while next_start < end and markdown[next_start].isspace():
                next_start += 1
            word_adjusted = True
            continue
        if not markdown[next_start].isspace() and not markdown[next_start - 1].isspace():
            while next_start < end and not markdown[next_start].isspace():
                next_start += 1
            word_adjusted = True
            continue
        break
    adjustment = ("word_and_atomic_boundary" if word_adjusted and atom_adjusted
                  else "word_boundary" if word_adjusted
                  else "atomic_boundary" if atom_adjusted else "none")
    return next_start, adjustment


def _ranges(markdown: str, size: int, overlap: int, atomic: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ranges, start = [], 0
    previous_end = 0
    while start < len(markdown):
        end = min(start + size, len(markdown))
        # Cuts must leave room for the requested overlap and add new content.
        # Otherwise high overlap can produce chunks wholly inside the previous.
        minimum_end = max(start + overlap, previous_end)
        if end < len(markdown):
            # Prefer complete paragraphs, then lines, then words.
            window = markdown[start:end]
            for delimiter in ("\n\n", "\n", " "):
                cut = window.rfind(delimiter)
                if cut >= size // 2 and start + cut + len(delimiter) > minimum_end:
                    end = start + cut + len(delimiter)
                    break
        for a, b in atomic:
            if a < end < b:
                end = a if a > minimum_end else b
                break
        if len(ranges) >= 200_000:
            raise ValueError("Quantidade de chunks excede limite")
        ranges.append((start, end))
        if end >= len(markdown):
            break
        # Always progress, even with overlap near the requested size.
        next_start, _ = _overlap_start(markdown, start, end, overlap, atomic)
        start = next_start
        previous_end = end
    return ranges


def _iter_chunks(document: Document, markdown: str, *, size_chars: int = 6000,
                 overlap_chars: int = 600) -> Iterator[dict[str, Any]]:
    if type(size_chars) is not int or not 256 <= size_chars <= 200_000:
        raise ValueError("Tamanho de chunk deve ter de 256 a 200000 caracteres")
    if type(overlap_chars) is not int or not 0 <= overlap_chars < size_chars:
        raise ValueError("Sobreposição deve ser menor que o tamanho do chunk")
    refs, atomic = _layout(document, markdown)
    atomic.extend((begin, end) for begin, end, _ in _advanced_images(markdown, refs))
    unclosed: list[dict[str, Any]] = []
    prose = [(ref["char_start"], ref["char_end"]) for ref in refs
             if ref["kind"] == "text" and ref["engine"] in {"pymupdf", "tesseract"}]
    atomic = _protected(markdown, atomic, blocks=[(ref["char_start"], ref["char_end"]) for ref in refs],
                        unclosed=unclosed, confined=prose)
    flag_starts = [item["start"] for item in unclosed]  # `_protected` leaves them sorted by start
    flag_reach, top = [], -1
    for item in unclosed:
        top = max(top, item["end"])
        flag_reach.append(top)

    def unclosed_touching(start: int, end: int) -> list[dict[str, Any]]:
        first = bisect.bisect_right(flag_reach, start)  # first item whose running end passes `start`
        return [item for item in unclosed[first:bisect.bisect_left(flag_starts, end)] if item["end"] > start]

    headings = []
    for match in re.finditer(r"(?m)^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", markdown):
        if not any(a <= match.start() < b for a, b in atomic):
            headings.append((match.start(), len(match.group(1)), match.group(2)))
    markdown_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    ranges = _ranges(markdown, size_chars, overlap_chars, atomic)
    for index, (start, end) in enumerate(ranges, 1):
        selected = [dict(ref) for ref in refs if ref["char_start"] < end and ref["char_end"] > start]
        context: list[dict[str, Any]] = []
        for offset, level, title in headings:
            if offset > start:
                break
            context = [heading for heading in context if heading["level"] < level]
            context.append({"level": level, "title": title})
        warnings = list(document.warnings)
        for ref in selected:
            warnings.extend(ref["warnings"])
        if end - start > size_chars:
            warnings.append("chunk_atomic_exceeds_target_chars")
        warnings.extend("chunk_unclosed_" + item["kind"] for item in unclosed_touching(start, end))
        actual_overlap = ranges[index - 2][1] - start if index > 1 else 0
        adjustment = "none" if index > 1 else "first_chunk"
        if index > 1 and actual_overlap < overlap_chars:
            _, adjustment = _overlap_start(markdown, *ranges[index - 2], overlap_chars, atomic)
            warnings.append("chunk_overlap_reduced")
        identity = f"{document.source_sha256}:{markdown_hash}:{start}:{end}".encode("ascii")
        yield {"schema_version": 1, "chunk_id": hashlib.sha256(identity).hexdigest(),
                       "index": index, "source_name": document.source_name,
                       "source_sha256": document.source_sha256, "markdown_sha256": markdown_hash,
                       "source_markdown": "../document.md", "asset_base": "..",
                       "offset_unit": "unicode_code_points", "char_start": start,
                       "char_end": end, "char_count": end - start,
                       "target_size_chars": size_chars, "requested_overlap_chars": overlap_chars,
                       "actual_overlap_chars": actual_overlap, "overlap_adjustment": adjustment,
                       "total": len(ranges),
                       "headings": context,
                       "headings_in_chunk": [{"level": level, "title": title, "char_start": offset}
                                             for offset, level, title in headings if start <= offset < end],
                       "pages": sorted({ref["page"] for ref in selected}), "blocks": selected,
                       "document_status": document.status, "fidelity": "unverified",
                       "warnings": list(dict.fromkeys(warnings)), "text": markdown[start:end]}


def generate_chunks(document: Document, markdown: str, *, size_chars: int = 6000,
                    overlap_chars: int = 600) -> list[dict[str, Any]]:
    """List API for callers; the disk writer consumes the iterator directly."""
    return list(_iter_chunks(document, markdown, size_chars=size_chars, overlap_chars=overlap_chars))


def write_chunks(document: Document, markdown: str, directory: Path, *, size_chars: int = 6000,
                 overlap_chars: int = 600, max_output_bytes: int | None = None) -> dict[str, Any]:
    """Write a new chunks directory; no overwrites, YAML library or token claims."""
    chunks = _iter_chunks(document, markdown, size_chars=size_chars, overlap_chars=overlap_chars)
    effective = layout(document)
    review_notice = effective.review_notice
    advanced_images = _advanced_images(markdown, effective.refs)
    directory = Path(directory)
    if directory.exists() or directory.is_symlink():
        raise ValueError("Pasta de chunks já existe")
    if max_output_bytes is not None and (type(max_output_bytes) is not int or max_output_bytes < 1):
        raise ValueError("Limite de saída de chunks inválido")
    directory.mkdir()
    total_bytes = 0
    created: list[Path] = []

    def write(name: str, text: str, stream=None) -> None:
        nonlocal total_bytes
        payload = text.encode("utf-8")
        total_bytes += len(payload)
        if max_output_bytes is not None and total_bytes > max_output_bytes:
            raise ValueError("Chunks excedem limite de saída")
        if stream is not None:
            stream.write(payload)
            return
        path = directory / name
        with path.open("xb") as stream:
            created.append(path)
            stream.write(payload)

    try:
        count, overlap_min, overlap_max, reduced = 0, None, None, 0
        with (directory / "chunks.jsonl").open("xb") as jsonl:
            created.append(directory / "chunks.jsonl")
            for chunk in chunks:
                count += 1
                if count > 1:
                    actual = chunk["actual_overlap_chars"]
                    overlap_min = actual if overlap_min is None else min(overlap_min, actual)
                    overlap_max = actual if overlap_max is None else max(overlap_max, actual)
                    reduced += int(actual < overlap_chars)
                write("chunks.jsonl", json.dumps(chunk, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", stream=jsonl)
                metadata = {key: chunk[key] for key in ("chunk_id", "index", "total", "source_name",
                                                       "source_sha256", "markdown_sha256", "source_markdown",
                                                       "char_start", "char_end", "offset_unit",
                                                       "requested_overlap_chars", "actual_overlap_chars", "overlap_adjustment",
                                                       "document_status", "fidelity", "pages", "warnings")}
                # JSON scalars/arrays are valid YAML values and quote hostile titles safely.
                header = "---\n" + "\n".join(key + ": " + json.dumps(value, ensure_ascii=False)
                                               for key, value in metadata.items()) + "\n---\n\n"
                # JSONL remains the exact source slice; Markdown files use links relative
                # to their directory so embedded local images continue to work.
                body = chunk["text"]
                # Rebase only images actually emitted by the renderer. Literal code,
                # user Markdown and marker-looking source text must remain untouched.
                edits = []
                for ref in sorted(chunk["blocks"], key=lambda r: r["char_start"], reverse=True):
                    if ref["kind"] != "image" or not ref.get("asset"):
                        continue
                    begin, end = ref["char_start"] - chunk["char_start"], ref["char_end"] - chunk["char_start"]
                    if not 0 <= begin < end <= len(chunk["text"]):
                        continue
                    original = chunk["text"][begin:end]
                    derivative = original.replace("(" + ref["asset"] + ")", "(../" + ref["asset"] + ")")
                    edits.append((begin, end, derivative))
                # An advanced block may span hundreds of chunks. Rebase each whole
                # exported image wherever its own span intersects a source slice.
                for image_start, image_end, asset in advanced_images:
                    begin, end = image_start - chunk["char_start"], image_end - chunk["char_start"]
                    if 0 <= begin < end <= len(chunk["text"]):
                        edits.append((begin, end, "![Imagem reconstruída](../assets/" + asset + ")"))
                # Rebase only the generated review note, using its known span.
                # Source links and literal code that happen to match stay intact.
                if review_notice:
                    begin, end = review_notice[0] - chunk["char_start"], review_notice[1] - chunk["char_start"]
                    if 0 <= begin < end <= len(chunk["text"]):
                        note = chunk["text"][begin:end].replace("(LEIA-AVISOS.txt)", "(../LEIA-AVISOS.txt)")
                        edits.append((begin, end, note))
                for begin, end, derivative in sorted(edits, reverse=True):
                    body = body[:begin] + derivative + body[end:]
                write(f"{chunk['index']:04d}.md", header + body)
        summary = {"schema_version": 1, "count": count, "source_sha256": document.source_sha256,
                   "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                   "size_chars": size_chars, "overlap_chars": overlap_chars,
                   "actual_overlap_min_chars": overlap_min, "actual_overlap_max_chars": overlap_max,
                   "overlap_reduced_transitions": reduced,
                   "offset_unit": "unicode_code_points", "jsonl": "chunks.jsonl",
                   "markdown_links": "local_assets_relative_to_parent", "fidelity": "unverified"}
        write("manifest.json", json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return {**summary, "bytes": total_bytes}
    except Exception:
        # Preserve any unrelated file added concurrently to this new directory.
        for path in created:
            try:
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise
