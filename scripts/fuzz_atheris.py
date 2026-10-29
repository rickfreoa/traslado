# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Harness de fuzzing guiado por cobertura com Atheris para o Traslado 2.0.0.

Procedimento para converter achado do fuzz noturno em fixture bloqueante de regressão por PR:
1. Baixe o artefato `fuzz-findings-<alvo>` publicado pelo workflow `.github/workflows/fuzz.yml`.
2. Reproduza localmente a falha com:
     python scripts/fuzz_atheris.py --target <ext> --reproduce <arquivo-achado>
3. Ajuste a classificação/tratamento no parser correspondente (sem produzir `error_code="internal"`
   nem exceção não tratada).
4. Adicione o payload minimizado em `tests/` (por exemplo em `tests/test_errors_e3.py` ou
   `tests/test_mutation_e6.py`) garantindo que `execute()` retorne status e `error_code`
   classificados (`malformed`, `resource_limit`, etc.) e abra PR bloqueado por `ci.yml`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

TARGETS = ("txt", "csv", "json", "xml", "html", "docx", "xlsx", "pptx", "pdf", "png")


def _record_finding(artifacts_dir: Path, target: str, data: bytes, reason: str, result: dict | None = None) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    raw_path = artifacts_dir / f"finding-{target}-{digest}.{target}"
    meta_path = artifacts_dir / f"finding-{target}-{digest}.json"
    raw_path.write_bytes(data)
    meta_path.write_text(
        json.dumps(
            {
                "target": target,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "reason": reason,
                "result": result,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return raw_path


def run_single_input(target: str, data: bytes, work_dir: Path, artifacts_dir: Path) -> None:
    from traslado.model import Options, Document
    from traslado.formats import extract
    from traslado.errors import classify, partial_error

    if len(data) > 256 * 1024:
        return
    src = work_dir / f"input.{target}"
    out = work_dir / "out"
    src.write_bytes(data)
    opts = Options(
        mode="native",
        images=False,
        tables=False,
        chunks=False,
        job_timeout=5,
        ocr_timeout=5,
    )
    try:
        out.mkdir(parents=True, exist_ok=True)
        document = Document(src.name, hashlib.sha256(data).hexdigest())
        extract(src, out, opts, document)
        issue = partial_error(document)
        if issue and issue.code == "internal":
            raise RuntimeError(str(issue))
        res = {"status": document.status, "error_code": issue.code if issue else None}
    except Exception as exc:
        issue = classify(exc)
        if issue.code != "internal":
            return
        saved = _record_finding(artifacts_dir, target, data, f"unhandled_exception: {type(exc).__name__}: {exc}")
        raise AssertionError(f"Exceção não tratada salva em {saved}: {exc}") from exc
    finally:
        src.unlink(missing_ok=True)
        shutil.rmtree(out, ignore_errors=True)

    status = res.get("status")
    code = res.get("error_code")
    if status == "failed" and code in {None, "internal"}:
        saved = _record_finding(artifacts_dir, target, data, f"unclassified_failure: {code}", res)
        raise AssertionError(f"Falha interna não classificada salva em {saved}: {res}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=TARGETS, default="docx", help="Formato alvo do fuzzing")
    ap.add_argument("--max-total-time", type=int, default=1200, help="Tempo máximo em segundos (padrão: 1200 = 20 min)")
    ap.add_argument("--runs", type=int, default=0, help="Número máximo de execuções do libFuzzer (0 = ilimitado até timeout)")
    ap.add_argument("--artifacts-dir", type=Path, default=ROOT / "fuzz-artifacts", help="Diretório para salvar achados")
    ap.add_argument("--reproduce", type=Path, default=None, help="Reproduz um arquivo de achado específico sem Atheris")
    args, extra = ap.parse_known_args(argv)

    if not 1 <= args.max_total_time <= 1200:
        ap.error("--max-total-time deve ficar entre 1 e 1200 segundos")
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"traslado-fuzz-{args.target}-") as tmp:
        work_dir = Path(tmp)
        if args.reproduce is not None:
            payload = args.reproduce.read_bytes()
            run_single_input(args.target, payload, work_dir, args.artifacts_dir)
            print(f"Reprodução concluída sem falha interna para {args.reproduce}")
            return 0

        try:
            import atheris  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Atheris obrigatório: fuzz não executado") from exc

        with atheris.instrument_imports():
            import traslado.formats  # noqa: F401
            import traslado.pdf  # noqa: F401
            import traslado.runner  # noqa: F401

        sys.path.insert(0, str(ROOT))
        from tests.test_mutation_e6 import _native_fixtures
        corpus = work_dir / "corpus"
        corpus.mkdir()
        seeds = _native_fixtures()
        if args.target not in seeds:
            raise RuntimeError(f"Fixture obrigatória ausente: {args.target}")
        (corpus / "seed").write_bytes(seeds[args.target])
        fuzzer_args = [
            sys.argv[0],
            f"-max_total_time={args.max_total_time}",
            "-timeout=30",
            "-max_len=262144",
            "-rss_limit_mb=2048",
            str(corpus),
            f"-artifact_prefix={args.artifacts_dir.as_posix()}/",
        ]
        if args.runs > 0:
            fuzzer_args.append(f"-runs={args.runs}")
        fuzzer_args.extend(extra)

        @atheris.instrument_func
        def _test_one(data: bytes) -> None:
            run_single_input(args.target, data, work_dir, args.artifacts_dir)

        atheris.Setup(fuzzer_args, _test_one)
        atheris.Fuzz()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
