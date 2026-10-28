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
from . import lifecycle
from .model import Options
from . import platform as plat
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

    setup = sub.add_parser("setup", help="Configurar release candidata, gates e promoção")
    setup.add_argument("--release-id")
    setup.add_argument("--expected-digests", type=Path)
    setup.add_argument("--check-active", action="store_true")
    setup.add_argument("--expected-version", default=__version__)
    setup.add_argument("--profile", choices=["Full", "Core", "full", "core"], default="Full")
    setup.add_argument("--extras")
    setup.add_argument("--languages", default=",".join(lifecycle.DEFAULT_LANGUAGES))
    setup.add_argument("--no-shortcut", action="store_true")
    setup.add_argument("--root", type=Path)
    setup.add_argument("--promote", action=argparse.BooleanOptionalAction, default=False)
    setup.add_argument("--allow-offline-promotion", action="store_true")
    setup.add_argument("--from-dir", type=Path)
    setup.add_argument("--skip-ocr", action="store_true")
    setup.add_argument("--ocr-cache", type=Path)
    setup.add_argument("--data-root", type=Path)

    where = sub.add_parser("where", help="Localizar instalação ativa e candidata")
    where.add_argument("--json", action="store_true")
    where.add_argument("--root", type=Path)

    update = sub.add_parser("update", help="Atualizar instalação pelo GitHub Releases")
    update.add_argument("--version")
    update.add_argument("--yes", action="store_true")
    update.add_argument("--root", type=Path)

    uninstall = sub.add_parser("uninstall", help="Remover atalhos e raiz gerenciada")
    uninstall.add_argument("--yes", action="store_true")
    uninstall.add_argument("--keep-data", action="store_true")
    uninstall.add_argument("--root", type=Path)

    rollback = sub.add_parser("rollback", help="Reverter para a release anterior")
    rollback.add_argument("--root", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(inspect(args.tesseract, args.tessdata), ensure_ascii=False, indent=2))
            return 0
        if args.command == "gui":
            from .gui import main as gui
            gui()
            return 0
        if args.command == "setup":
            if args.check_active:
                return 0 if lifecycle.active_release_matches(args.root or plat.managed_root(), args.expected_version,
                    "Core" if args.profile.lower() == "core" else "Full", args.extras, args.languages) else 1
            manifest = lifecycle.setup_release(
                root=args.root,
                release_id=args.release_id,
                profile="Core" if args.profile.lower() == "core" else "Full",
                extras=args.extras,
                languages=args.languages,
                no_shortcut=args.no_shortcut,
                promote=bool(args.promote),
                allow_offline_promotion=args.allow_offline_promotion,
                skip_ocr=args.skip_ocr,
                from_dir=args.from_dir,
                expected_digests=json.loads(args.expected_digests.read_text(encoding="utf-8-sig")) if args.expected_digests else None,
                ocr_cache=args.ocr_cache,
                data_root=args.data_root,
            )
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            gates = manifest.get("gates", {})
            if gates.get("doctor") is not True or gates.get("fixture") is not True:
                return 2
            return 0
        if args.command == "where":
            info = plat.where(args.root)
            if args.json:
                print(json.dumps(info, ensure_ascii=False))
            else:
                print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0 if info.get("current") is not None else 1
        if args.command == "update":
            res = lifecycle.update_release(version=args.version, yes=args.yes, root=args.root)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if args.command == "uninstall":
            res = lifecycle.uninstall_managed(root=args.root, yes=args.yes, keep_data=args.keep_data)
            print(json.dumps(res, ensure_ascii=False, indent=2))
            return 0
        if args.command == "rollback":
            target_root = args.root if args.root is not None else plat.managed_root()
            restored = plat.rollback(target_root)
            print(json.dumps(restored, ensure_ascii=False, indent=2))
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
