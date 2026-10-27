"""Experimental opt-in bridge to separately installed MinerU Kit 4.0.10.

No engine/model is bundled. The bridge is a process boundary, not an OS sandbox.
HF offline variables prevent normal hub downloads, not arbitrary third-party IO.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile

from .formats import image_to_png
from .model import Block, Document, Options
from .safety import is_link, run_bounded


class PassiveHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in {"table", "thead", "tbody", "tr", "td", "th", "br"}:
            safe = []
            for key, value in attrs:
                if key in {"colspan", "rowspan"} and value and value.isdigit() and 1 <= int(value) <= 1000:
                    safe.append(f' {key}="{int(value)}"')
            self.out.append("<" + tag + "".join(safe) + ">")
        else:
            self.out.append(html.escape(self.get_starttag_text()))

    def handle_endtag(self, tag):
        self.out.append("</" + tag + ">" if tag in {"table", "thead", "tbody", "tr", "td", "th"} else html.escape("</" + tag + ">"))

    def handle_startendtag(self, tag, attrs):
        # HTMLParser's default invents an end tag, including a visible escaped
        # </br> for <br/>. Keep void breaks canonical and other safe tags closed
        # with their original self-closing form; unknown tags remain literal.
        if tag in {"table", "thead", "tbody", "tr", "td", "th", "br"}:
            self.handle_starttag(tag, attrs)
            if tag != "br":
                self.out[-1] = self.out[-1][:-1] + "/>"
        else:
            self.out.append(html.escape(self.get_starttag_text()))

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        self.out.append(html.escape("<!--" + data + "-->"))

    def handle_decl(self, decl):
        self.out.append(html.escape("<!" + decl + ">"))

    def handle_pi(self, data):
        self.out.append(html.escape("<?" + data + ">"))

    def unknown_decl(self, data):
        self.out.append(html.escape("<![" + data + "]>"))

    def handle_entityref(self, name):
        self.out.append("&" + name + ";")

    def handle_charref(self, name):
        self.out.append("&#" + name + ";")


def sanitize(markdown: str, model_root: Path, workspace: Path, page_number: int, options: Options) -> str:
    """Literalize model link syntax; retain only our reconstructed local images.

    This deliberately sacrifices Markdown link/reference formatting instead of
    trying to recognize CommonMark with regular expressions. Nothing is deleted
    merely because it resembles a reference definition. The narrow image regex
    is an optional importer, never a security filter: unmatched syntax remains
    literal. Markers cannot collide with raw or entity-decoded model text.
    """
    image_count = 0
    marker_prefix = "FREOAASSETMARKER0X"
    decoded = html.unescape(markdown)
    marker_number = 0
    while marker_prefix in markdown or marker_prefix in decoded:
        marker_number += 1
        marker_prefix = f"FREOAASSETMARKER{marker_number}X"
    markers = {}
    root = model_root.resolve()

    def local_image(match):
        nonlocal image_count
        relative = match.group(1)
        parts = Path(relative).parts
        if (not options.images or not re.fullmatch(r"[a-zA-Z0-9_.\-/]+", relative)
                or relative.startswith("/") or any(p in {".", ".."} for p in parts)):
            return match.group(0)
        file = root / relative
        # Check before resolving, including every intermediate directory.
        if any(is_link(p) for p in [model_root, file, *file.parents] if p == root or root in p.parents or p == model_root):
            return match.group(0)
        file = file.resolve()
        if root not in file.parents or not file.is_file() or file.stat().st_size > options.max_output_bytes:
            return match.group(0)
        image_count += 1
        target = workspace / "assets" / f"advanced-{page_number}-{image_count}.png"
        try:
            image_to_png(file, target, options)
        except (OSError, ValueError):
            target.unlink(missing_ok=True)
            raise
        marker = f"{marker_prefix}{image_count}END"
        markers[marker] = f"![Imagem reconstruída](assets/{target.name})"
        return marker

    markdown = re.sub(r'!\[[^\]\n]*\]\(([^)\n]*)\)', local_image, markdown)
    parser = PassiveHTML()
    parser.feed(markdown)
    parser.close()
    passive = "".join(parser.out).replace("[", "&#91;").replace("]", "&#93;")
    for marker, image in markers.items():
        passive = passive.replace(marker, image)
    return passive


def refine(source: Path, workspace: Path, options: Options, document: Document) -> None:
    if not options.advanced_kit:
        return
    executable = Path(options.advanced_kit)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("Informe caminho absoluto de mineru-kit instalado separadamente")
    import fitz
    targets = [p for p in document.pages if p.route == "ocr" or any(
        w.startswith(("region_ocr_failed", "pdf_table_merged", "reading_order_two_column", "table_detection_failed")) for w in p.warnings)]
    if not targets:
        return
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    with tempfile.TemporaryDirectory(prefix="advanced-", dir=workspace) as name:
        temporary = Path(name)
        version_log = temporary / "version.log"
        code = run_bounded([str(executable), "--version"], temporary, 10, version_log, env=env)
        version = version_log.read_text(encoding="utf-8", errors="replace")
        if code != 0 or not re.search(r"(?<![\d.])4\.0\.10(?![\d.])", version):
            document.warnings.append("advanced_unsupported_version_expected_4.0.10")
            return
        with fitz.open(source) as pdf:
            for page in targets:
                folder = temporary / f"page-{page.number}"
                folder.mkdir()
                selected = folder / "selected.pdf"
                with fitz.open() as one:
                    one.insert_pdf(pdf, from_page=page.number-1, to_page=page.number-1)
                    one.save(selected)
                output = folder / "result.md"
                try:
                    code = run_bounded([str(executable), "parse", str(selected), "-o", str(output),
                                        "--tier", "advanced"], folder, options.advanced_timeout, folder / "parse.log", env=env)
                    if code != 0 or not output.is_file() or output.stat().st_size > options.max_output_bytes:
                        raise RuntimeError("MinerU não produziu resultado válido; mantenha modelos locais preparados")
                    content = output.read_text(encoding="utf-8")
                    if not content.strip():
                        raise RuntimeError("MinerU produziu resultado vazio")
                    passive = sanitize(content, folder, workspace, page.number, options)
                    # Keep previous extraction as an explicit alternative, never erase provenance.
                    # T11: line geometry is internal (never serialized); role only when classified.
                    previous = [{k: v for k, v in b.__dict__.items()
                                 if k != "lines" and not (k == "role" and v is None)} for b in page.blocks]
                    page.blocks = [Block("text", passive, engine="mineru-kit-4.0.10",
                                         details={"tier": "advanced", "previous_blocks": previous,
                                                  "source_page": page.number, "model_revision": "unverified",
                                                  "raw_model_markdown": content,
                                                  "markdown_policy": "literal_links_and_references_local_images_only"})]
                    page.route = "advanced"
                    page.warnings.append("model_reconstruction_requires_source_review")
                    page.warnings.append("model_markdown_links_literalized_formatting_partial")
                except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                    page.warnings.append("advanced_failed: " + str(exc))
