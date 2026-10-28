# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

from .errors import ConversionError, classify

from . import __version__
from .doctor import inspect
from .model import Options
from .runner import discover, execute


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"status": "failed", "error": message, "error_code": "malformed"},
                         ensure_ascii=False), file=sys.stderr)
        self.exit(2)


def main(argv: list[str] | None = None) -> int:
    # JSON transport remains UTF-8 when Windows redirects stdout to a pipe.
    # -I deliberately ignores PYTHONIOENCODING supplied by the installer.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = _Parser(description="Traslado — conversão local")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Verificar instalação e capacidades")
    doctor.add_argument("--tesseract")
    doctor.add_argument("--tessdata")
    convert = sub.add_parser("convert", help="Converter arquivo ou pasta")
    convert.add_argument("source", type=Path)
    convert.add_argument("-o", "--output", type=Path, required=True)
    convert.add_argument("--recursive", action="store_true")
    convert.add_argument("--mode", choices=["auto", "native", "ocr"])
    convert.add_argument("--engine", choices=["legacy"], help="Ativar motor legado experimental local")
    convert.add_argument("--furniture", choices=["off", "mark"])
    convert.add_argument("--language")
    convert.add_argument("--dpi", type=int)
    convert.add_argument("--job-timeout", type=int)
    convert.add_argument("--ocr-timeout", type=int)
    convert.add_argument("--ocr-workers", type=int)
    convert.add_argument("--tesseract")
    convert.add_argument("--tessdata")
    convert.add_argument("--advanced-kit", help="Experimental: caminho absoluto do MinerU Kit 4.0.10 local")
    convert.add_argument("--advanced-timeout", type=int)
    convert.add_argument("--config", type=Path)
    convert.add_argument("--no-images", action="store_true")
    convert.add_argument("--scan-previews", action="store_true", help="Incluir prévias reduzidas das páginas escaneadas")
    convert.add_argument("--chunks", action=argparse.BooleanOptionalAction, default=None, help="Gerar chunks com proveniência para IA/RAG")
    convert.add_argument("--overwrite", action=argparse.BooleanOptionalAction, default=None, help="Substituir resultado anterior íntegro da mesma entrada")
    convert.add_argument("--chunk-size-chars", type=int)
    convert.add_argument("--chunk-overlap-chars", type=int)
    convert.add_argument("--no-tables", action="store_true", help="Desativar detecção geométrica de tabelas PDF; células Office/CSV são preservadas")
    sub.add_parser("gui", help="Abrir interface")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(inspect(args.tesseract, args.tessdata), ensure_ascii=False, indent=2))
            return 0
        if args.command == "gui":
            from .gui import main as gui
            gui()
            return 0
        values = {}
        if args.config:
            if args.config.stat().st_size > 64 * 1024:
                raise ConversionError('resource_limit', "Configuração excede limite")
            data = tomllib.loads(args.config.read_text(encoding="utf-8"))
            if set(data) != {"conversion"} or not isinstance(data["conversion"], dict):
                raise ValueError("Use somente a seção [conversion]")
            values.update(data["conversion"])
        for name in ("mode", "furniture", "language", "dpi", "job_timeout", "ocr_timeout", "ocr_workers", "tesseract", "tessdata", "advanced_kit", "advanced_timeout",
                     "chunks", "overwrite", "chunk_size_chars", "chunk_overlap_chars"):
            value = getattr(args, name)
            if value is not None:
                values[name] = value
        if args.no_images:
            values["images"] = False
        if args.scan_previews:
            values["scan_previews"] = True
        if args.no_tables:
            values["tables"] = False
        if args.engine == "legacy":
            values["legacy"] = True
        options = Options(**values)
        files = discover(args.source, args.output, args.recursive)
        if not files:
            raise ConversionError("unsupported", "Nenhum arquivo suportado encontrado")
        results = []
        for file in files:
            result = execute(file, args.output, options)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        statuses = {r["status"] for r in results}
        return 2 if statuses & {"failed", "cancelled", "timeout"} else 3 if "partial" in statuses else 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "error_code": ("malformed" if isinstance(exc, (ValueError, TypeError)) and not isinstance(exc, ConversionError) else classify(exc).code)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
