from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
from typing import Any

from .model import Block, Document, Options, Page, finite_box, text_metrics
from .ocr import recognize, recognize_blocks


def reading_order(blocks: list[Block], width: float) -> tuple[list[Block], bool]:
    mid = width / 2
    # Input indices keep distinct equal-valued blocks distinct and make membership
    # disjoint. Narrow central blocks belong to the spanning set, never both sides.
    left, right, spanning = [], [], []
    for index, block in enumerate(blocks):
        on_left = bool(block.bbox and block.bbox[2] <= mid + 3)
        on_right = bool(block.bbox and block.bbox[0] >= mid - 3)
        if on_left and not on_right:
            left.append(index)
        elif on_right and not on_left:
            right.append(index)
        else:
            spanning.append(index)
    overlap = bool(left and right and min(max(blocks[i].bbox[3] for i in left), max(blocks[i].bbox[3] for i in right)) >
                   max(min(blocks[i].bbox[1] for i in left), min(blocks[i].bbox[1] for i in right)))
    chars = sum(len(b.text) for b in blocks) or 1
    columns = (len(left) >= 2 and len(right) >= 2 and overlap and
               sum(len(blocks[i].text) for i in left) / chars >= .2 and
               sum(len(blocks[i].text) for i in right) / chars >= .2)
    key = lambda i: (blocks[i].bbox[1] if blocks[i].bbox else 0,
                     blocks[i].bbox[0] if blocks[i].bbox else 0, i)
    if not columns:
        return [blocks[i] for i in sorted(range(len(blocks)), key=key)], False
    out = []
    left_remaining, right_remaining = left[:], right[:]
    for span in sorted(spanning, key=key):
        y = blocks[span].bbox[1] if blocks[span].bbox else 0
        left_before = [i for i in left_remaining if blocks[i].bbox[1] < y]
        right_before = [i for i in right_remaining if blocks[i].bbox[1] < y]
        out.extend(sorted(left_before, key=key))
        out.extend(sorted(right_before, key=key))
        out.append(span)
        left_remaining = [i for i in left_remaining if i not in left_before]
        right_remaining = [i for i in right_remaining if i not in right_before]
    out.extend(sorted(left_remaining, key=key))
    out.extend(sorted(right_remaining, key=key))
    return [blocks[i] for i in out], True


def render_page(page: Any, path: Path, options: Options, clip: Any = None) -> None:
    import fitz
    rect = clip or page.rect
    scale = options.dpi / 72
    if (rect.width * scale + 1) * (rect.height * scale + 1) > options.max_pixels:
        raise ValueError("Página excede limite de pixels; reduza o DPI")
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    if pix.width * pix.height > options.max_pixels:
        raise ValueError("Renderização excede limite de pixels")
    pix.save(path)


def render_scan_preview(page: Any, path: Path, options: Options) -> None:
    """Optional compact, lossy preview; never used as the OCR input."""
    import fitz
    rect = page.rect
    scale = min(options.dpi / 72, 1200 / max(rect.width, rect.height))
    if (rect.width * scale + 1) * (rect.height * scale + 1) > options.max_pixels:
        raise ValueError("Prévia excede limite de pixels")
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    if pix.width * pix.height > options.max_pixels:
        raise ValueError("Prévia excede limite de pixels")
    pix.save(path, jpg_quality=85)


def _within(block: Block, box: Any) -> bool:
    b = block.bbox
    return bool(b and box[0] <= (b[0]+b[2])/2 <= box[2] and box[1] <= (b[1]+b[3])/2 <= box[3])


def _contained(block: Block, box: Any) -> bool:
    b = block.bbox
    return bool(b and box[0] - .5 <= b[0] and box[1] - .5 <= b[1] and
                b[2] <= box[2] + .5 and b[3] <= box[3] + .5)


def _intersects(block: Block, box: Any) -> bool:
    b = block.bbox
    return bool(b and min(b[2], box[2]) > max(b[0], box[0]) and min(b[3], box[3]) > max(b[1], box[1]))


def _progress(workspace: Path, number: int, total: int, route: str) -> None:
    temporary = workspace / ".progress.json.tmp"
    try:
        temporary.write_text(json.dumps({"page": number, "total": total, "route": route}), encoding="utf-8")
        temporary.replace(workspace / "progress.json")
    except OSError:
        # A GUI progress reader can briefly deny Windows replacement. Progress
        # is advisory: preserve its last complete value and keep converting.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def extract(path: Path, workspace: Path, options: Options, document: Document) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Instale o perfil PDF: pip install .[pdf]") from exc
    assets = workspace / "assets"
    assets.mkdir(exist_ok=True)
    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise ValueError("PDF protegido por senha não é suportado")
        if len(pdf) > options.max_pages:
            raise ValueError("PDF excede limite de páginas")
        for index, source in enumerate(pdf, 1):
            _progress(workspace, index, len(pdf), "analyzing")
            raw = source.get_text("blocks", flags=fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES)
            blocks = [Block("text", str(b[4]).strip(), finite_box(b[:4]), engine="pymupdf")
                      for b in raw if len(b) >= 7 and b[6] == 0 and str(b[4]).strip()]
            native = "\n\n".join(b.text for b in blocks)
            metrics = text_metrics(native)
            images = source.get_image_info()
            rect = source.rect
            area = max(rect.width * rect.height, 1)
            image_boxes = [fitz.Rect(i["bbox"]) & rect for i in images]
            coverage = min(1., sum(max(0, b.width*b.height) for b in image_boxes) / area)
            metrics["image_coverage_upper_bound"] = coverage
            scan_boxes = [b for b in image_boxes if max(0, b.width*b.height) / area > .65]
            if scan_boxes:
                metrics["scan_page_image_in_source_pdf"] = True
                metrics["scan_previews_exported"] = False
                if native.strip():
                    metrics["scanned_page_with_native_text"] = True
            drawings = source.get_drawings() if options.tables or not native else []
            blank = not native.strip() and not images and not drawings
            needs_ocr = (options.mode == "ocr" and not blank) or (options.mode == "auto" and not blank and
                         (metrics["suspect"] or not native.strip() or (coverage > .65 and metrics["chars"] < 160)))
            page = Page(index, "blank" if blank else "native", metrics=metrics, native_text=native)
            # Appearance text may enter native extraction, but field identity/value
            # relationships and annotation text are not covered by that contract.
            if any(True for _ in (source.widgets() or ())):
                page.warnings.append("pdf_form_field_semantics_unverified")
            if any(True for _ in (source.annots() or ())):
                page.warnings.append("pdf_annotations_not_extracted")
            document.pages.append(page)
            if needs_ocr:
                page.route = "ocr"
                _progress(workspace, index, len(pdf), page.route)
                if native.strip():
                    page.metrics["native_text_retained_in_json_ocr_supersedes_markdown"] = True
                try:
                    # One high-resolution OCR input at a time, outside the final
                    # package. A book must not accumulate a PNG for every page.
                    with tempfile.TemporaryDirectory(prefix=".freoa-ocr-page-", dir=workspace.parent) as raw:
                        image = Path(raw) / "page.png"
                        render_page(source, image, options)
                        scale = options.dpi / 72
                        raster_rect = (source.rect * fitz.Matrix(scale, scale)).irect
                        derotation = source.derotation_matrix

                        def transform(x: float, y: float) -> tuple[float, float]:
                            # Pixmap pixels start at its integer device origin.
                            # Native text boxes are unrotated and crop-local:
                            # cropbox.x0/y0 must NOT be added a second time.
                            point = fitz.Point((x + raster_rect.x0) / scale,
                                               (y + raster_rect.y0) / scale) * derotation
                            return point.x, point.y

                        ocr_blocks, warnings = recognize_blocks(image, workspace, options, transform)
                        for block in ocr_blocks:
                            block.details["source_page"] = index
                        page.blocks.extend(ocr_blocks)
                        page.warnings.extend(warnings)
                except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                    page.warnings.append("ocr_failed: " + str(exc))
                    page.blocks.extend(blocks)
                if options.images and options.scan_previews:
                    preview = assets / f"page-{index}-preview.jpg"
                    try:
                        render_scan_preview(source, preview, options)
                        page.blocks.append(Block("image", asset=f"assets/{preview.name}", engine="pymupdf",
                                                 details={"lossy_preview": True, "long_edge_pixels": 1200}))
                        page.metrics["scan_previews_exported"] = True
                    except (ValueError, OSError, RuntimeError) as exc:
                        preview.unlink(missing_ok=True)
                        page.warnings.append("scan_preview_failed: " + str(exc))
                if metrics["suspect"]:
                    page.warnings.append("native_unicode_suspect_retained_in_json")
                _progress(workspace, index, len(pdf), "ocr_done")
                continue
            if blank:
                _progress(workspace, index, len(pdf), "blank")
                continue
            if metrics["suspect"]:
                page.warnings.append("native_unicode_suspect")
            if options.tables and len(drawings) >= 3:
                try:
                    detected = source.find_tables(strategy="lines_strict")
                    for table in detected.tables:
                        rows = [["" if c is None else str(c) for c in row] for row in table.extract()]
                        if sum(len(r) for r in rows) > options.max_cells:
                            raise ValueError("Tabela excede limite de células")
                        box = finite_box(table.bbox)
                        if any(b.kind == "text" and _intersects(b, box) and not _contained(b, box) for b in blocks):
                            page.warnings.append("pdf_table_overlap_text_preserved_possible_duplication")
                        if any(b.kind == "table" and _intersects(b, box) for b in blocks):
                            page.warnings.append("pdf_overlapping_table_regions_need_review")
                        # A block crossing the table may contain an outside caption.
                        # Preserve it instead of silently deleting source content.
                        contained = [b for b in blocks if b.kind == "text" and _contained(b, box)]
                        native_chars = Counter(c for b in contained for c in b.text if not c.isspace())
                        table_chars = Counter(c for row in rows for cell in row for c in cell if not c.isspace())
                        if native_chars - table_chars:
                            # This is a conservative loss check, not proof that cell
                            # relationships or ordering match the source.
                            page.warnings.append("pdf_table_cell_text_mismatch_preserved")
                        else:
                            blocks = [b for b in blocks if b.kind != "text" or not _contained(b, box)]
                        blocks.append(Block("table", bbox=box, rows=rows, engine="pymupdf",
                                            details={"header_inferred": False, "strategy": "lines_strict"}))
                        if any(cell is None for row in table.rows for cell in row.cells):
                            page.warnings.append("pdf_table_merged_cells_need_review")
                except Exception as exc:
                    page.warnings.append("table_detection_failed: " + type(exc).__name__)
            blocks, columns = reading_order(blocks, rect.width)
            if columns:
                page.warnings.append("reading_order_two_column_heuristic")
            page.blocks.extend(blocks)
            for image_index, box in enumerate(image_boxes, 1):
                if box.is_empty or box.width*box.height < area * .01:
                    continue
                if max(0, box.width*box.height) / area > .65:
                    # Full-page scan images duplicate the source PDF. Ordinary
                    # illustrations still use their existing PNG export path.
                    if options.images and options.scan_previews and not page.metrics.get("scan_previews_exported"):
                        preview = assets / f"page-{index}-preview.jpg"
                        try:
                            render_scan_preview(source, preview, options)
                            page.blocks.append(Block("image", asset=f"assets/{preview.name}", engine="pymupdf",
                                                     details={"lossy_preview": True, "long_edge_pixels": 1200}))
                            page.metrics["scan_previews_exported"] = True
                        except (ValueError, OSError, RuntimeError) as exc:
                            preview.unlink(missing_ok=True)
                            page.warnings.append("scan_preview_failed: " + str(exc))
                    continue
                image_path = assets / f"page-{index}-image-{image_index}.png"
                # Rendering the displayed region preserves orientation and masks.
                if options.images:
                    try:
                        render_page(source, image_path, options, box)
                        page.blocks.append(Block("image", bbox=finite_box(box),
                                                 asset=f"assets/{image_path.name}", engine="pymupdf"))
                    except (ValueError, OSError, RuntimeError) as exc:
                        image_path.unlink(missing_ok=True)
                        page.warnings.append("image_render_failed: " + str(exc))
                region_has_native = any(_within(b, box) for b in blocks if b.kind == "text")
                if options.mode == "auto" and not region_has_native and box.width*box.height >= area * .15:
                    try:
                        if not image_path.exists():
                            render_page(source, image_path, options, box)
                        block, warnings = recognize(image_path, workspace, options)
                        block.bbox = finite_box(box)
                        block.details["region"] = True
                        if block.text:
                            page.blocks.append(block)
                            page.route = "native+region_ocr"
                        page.warnings.extend(warnings)
                    except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                        page.warnings.append("region_ocr_failed: " + str(exc))
                    if not options.images:
                        image_path.unlink(missing_ok=True)
            # Image placement is approximate and explicit in the report.
            if any(max(0, box.width*box.height) / area <= .65 for box in image_boxes):
                page.warnings.append("image_semantics_unverified")
            _progress(workspace, index, len(pdf), page.route)
