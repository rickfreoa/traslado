from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
from typing import Any

from . import __version__
from .runtime import default_language


@dataclass(frozen=True)
class Options:
    mode: str = "auto"
    language: str = field(default_factory=default_language)
    dpi: int = 250
    job_timeout: int = 3600
    ocr_workers: int = 4
    ocr_timeout: int = 45
    max_input_bytes: int = 256 * 1024 * 1024
    max_archive_bytes: int = 512 * 1024 * 1024
    max_pages: int = 5000
    max_pixels: int = 20_000_000
    max_output_bytes: int = 128 * 1024 * 1024
    max_cells: int = 1_000_000
    tesseract: str | None = None
    tessdata: str | None = None
    tables: bool = True
    images: bool = True
    scan_previews: bool = False
    chunks: bool = False
    overwrite: bool = False
    chunk_size_chars: int = 6000
    chunk_overlap_chars: int = 600
    advanced_kit: str | None = None
    advanced_timeout: int = 90
    max_memory_mb: int = 2048
    furniture: str = "mark"

    def __post_init__(self) -> None:
        if self.furniture not in ("off", "mark"):
            raise ValueError("Opção inválida: furniture")
        if self.mode not in {"auto", "native", "ocr"}:
            raise ValueError("Modo inválido")
        if not re.fullmatch(r"[a-zA-Z_]+(?:\+[a-zA-Z_]+)*", self.language):
            raise ValueError("Idioma OCR inválido")
        limits = {"ocr_workers": (1, 4), "dpi": (72, 400), "job_timeout": (1, 3600), "ocr_timeout": (1, 600),
                  "max_input_bytes": (1, 1_073_741_824), "max_archive_bytes": (1, 2_147_483_648),
                  "max_pages": (1, 10000), "max_pixels": (1000, 80_000_000),
                  "max_output_bytes": (100, 536_870_912), "max_cells": (1, 2_000_000),
                  "advanced_timeout": (1, 600), "max_memory_mb": (128, 16384),
                  "chunk_size_chars": (256, 100000), "chunk_overlap_chars": (0, 99999)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Limite inválido: {name}")
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("A sobreposição dos chunks deve ser menor que o tamanho")
        for name in ("tables", "images", "scan_previews", "chunks", "overwrite"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Opção inválida: {name}")
        for name in ("tesseract", "tessdata", "advanced_kit"):
            if getattr(self, name) is not None and not isinstance(getattr(self, name), str):
                raise ValueError(f"Caminho inválido: {name}")


@dataclass
class Block:
    kind: str
    text: str = ""
    bbox: list[float] | None = None
    asset: str | None = None
    engine: str = "native"
    rows: list[list[str]] | None = None
    confidence: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    role: str | None = None
    lines: list[dict] | None = None


@dataclass
class Page:
    number: int
    route: str
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    native_text: str | None = None


@dataclass
class Document:
    source_name: str
    source_sha256: str
    pages: list[Page] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "complete"
    schema_version: int = 1
    converter_version: str = __version__
    duration_seconds: float = 0
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for page in data["pages"]:
            for block in page["blocks"]:
                # Line geometry is internal to PDF analysis, never an output payload.
                block.pop("lines", None)
                if block.get("role") is None:
                    block.pop("role", None)
        return data


def text_metrics(text: str) -> dict[str, Any]:
    controls = sum(1 for c in text if (ord(c) < 32 and c not in "\n\r\t") or 127 <= ord(c) < 160)
    replacements = text.count("\ufffd")
    return {"chars": len(text.strip()), "words": len(text.split()),
            "controls": controls, "replacements": replacements,
            "suspect": controls > 0 or replacements > 0}


def finite_box(box: Any) -> list[float]:
    values = [float(x) for x in box]
    if len(values) != 4 or not all(math.isfinite(x) for x in values):
        raise ValueError("Caixa inválida")
    return values
