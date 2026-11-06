#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Runner de testes com faulthandler ativo, limite por teste e quarentena nominal."""
from __future__ import annotations

import argparse
import faulthandler
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable
import unittest

ROOT = Path(__file__).resolve().parent.parent


def ensure_tests_root_importable() -> Path | None:
    """Disponibiliza a raiz dos testes no sys.path de forma segura.

    Anexa a raiz ao final de sys.path para que módulos como 'tests.test_x' sejam
    importáveis pelo runner sem que 'traslado' seja resolvido contra 'src' nos jobs
    que testam o pacote instalado (onde 'traslado' vem do wheel no site-packages).
    """
    for candidate in [Path.cwd(), ROOT, Path(__file__).resolve().parent]:
        tests_dir = candidate / "tests"
        if tests_dir.is_dir():
            cand_str = str(candidate.resolve())
            if cand_str not in sys.path:
                sys.path.append(cand_str)
            return candidate.resolve()
    return None


ensure_tests_root_importable()

FORBIDDEN_QUARANTINE_TESTS = {
    "test_errors_e3.ErrorsE3.test_execute_failure_matrix",
    "tests.test_errors_e3.ErrorsE3.test_execute_failure_matrix",
}


def _matches_test(pattern: str, test_id: str) -> bool:
    if pattern == test_id:
        return True
    if test_id.endswith("." + pattern) or pattern.endswith("." + test_id):
        return True
    return False


def validate_quarantine_entry(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("Entrada de quarentena deve ser um objeto JSON")

    teste = entry.get("teste")
    if not isinstance(teste, str) or not teste.strip():
        raise ValueError("Campo 'teste' é obrigatório e não pode ser vazio")

    for proibido in FORBIDDEN_QUARANTINE_TESTS:
        if _matches_test(proibido, teste.strip()):
            raise ValueError(
                f"O teste {teste!r} permanece bloqueante em todas as células e não pode entrar em quarentena."
            )

    celula = entry.get("celula")
    if not isinstance(celula, dict):
        raise ValueError("Campo 'celula' é obrigatório e deve ser um objeto com {os, python, perfil}")
    for k in ("os", "python", "perfil"):
        val = celula.get(k)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Campo 'celula.{k}' é obrigatório e não pode ser vazio")

    issue = entry.get("issue")
    if not isinstance(issue, str) or not issue.strip():
        raise ValueError(f"Campo 'issue' é obrigatório para a entrada de quarentena de {teste!r}")

    motivo = entry.get("motivo")
    if not isinstance(motivo, str) or not motivo.strip():
        raise ValueError(f"Campo 'motivo' é obrigatório para a entrada de quarentena de {teste!r}")

    return {
        "teste": teste.strip(),
        "celula": {
            "os": celula["os"].strip(),
            "python": celula["python"].strip(),
            "perfil": celula["perfil"].strip(),
        },
        "issue": issue.strip(),
        "motivo": motivo.strip(),
    }


def load_and_validate_quarantine(path: Path | str | None = None) -> list[dict]:
    if path is None:
        path = ROOT / "ci" / "quarentena.json"
    p = Path(path)
    if not p.is_file():
        return []
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Arquivo de quarentena inválido ({p}): {exc}") from exc
    if not isinstance(dados, list):
        raise ValueError(f"Arquivo de quarentena deve conter uma lista JSON ({p})")
    return [validate_quarantine_entry(item) for item in dados]


def _norm_python_version(py: str) -> str:
    # "3.14.0" -> "3.14", "3.14" -> "3.14"
    parts = py.strip().split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return py.strip()


def _cell_matches(entry_cell: dict, target_cell: dict) -> bool:
    target_os = target_cell.get("os", "").lower()
    entry_os = entry_cell.get("os", "").lower()
    if entry_os != "*" and entry_os != target_os:
        # Tenta casar prefixos como 'windows' com 'windows-latest'
        if not (entry_os in target_os or target_os in entry_os):
            return False

    target_py = _norm_python_version(target_cell.get("python", ""))
    entry_py = _norm_python_version(entry_cell.get("python", ""))
    if entry_py != "*" and entry_py != target_py:
        return False

    target_perfil = target_cell.get("perfil", "").lower()
    entry_perfil = entry_cell.get("perfil", "").lower()
    if entry_perfil != "*" and entry_perfil != target_perfil:
        return False

    return True


def is_test_quarantined(test_id: str, current_cell: dict, quarantine_entries: list[dict]) -> bool:
    for proibido in FORBIDDEN_QUARANTINE_TESTS:
        if _matches_test(proibido, test_id):
            return False
    for entry in quarantine_entries:
        if _cell_matches(entry["celula"], current_cell) and _matches_test(entry["teste"], test_id):
            return True
    return False


def get_quarantined_tests_for_cell(current_cell: dict, quarantine_entries: list[dict]) -> set[str]:
    return {
        entry["teste"]
        for entry in quarantine_entries
        if _cell_matches(entry["celula"], current_cell)
        and not any(_matches_test(proibido, entry["teste"]) for proibido in FORBIDDEN_QUARANTINE_TESTS)
    }


class TimeoutTestResult(unittest.TextTestResult):
    """TextTestResult que ativa faulthandler.dump_traceback_later por teste."""

    def __init__(self, *args, per_test_timeout: float = 600.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.per_test_timeout = per_test_timeout

    def startTest(self, test):
        super().startTest(test)
        if self.per_test_timeout and self.per_test_timeout > 0:
            try:
                faulthandler.dump_traceback_later(self.per_test_timeout, repeat=False, exit=True)
            except Exception:
                pass

    def stopTest(self, test):
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        super().stopTest(test)


def filter_suite(suite: unittest.TestSuite, predicate: Callable[[unittest.TestCase], bool]) -> unittest.TestSuite:
    new_suite = unittest.TestSuite()
    for item in suite:
        if isinstance(item, unittest.TestCase):
            if predicate(item):
                new_suite.addTest(item)
        elif isinstance(item, unittest.TestSuite):
            sub = filter_suite(item, predicate)
            if sub.countTestCases() > 0:
                new_suite.addTest(sub)
    return new_suite


def run_tests_with_runner(
    suite: unittest.TestSuite,
    *,
    timeout: float = 600.0,
    verbosity: int = 1,
) -> bool:
    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        resultclass=lambda *args, **kwargs: TimeoutTestResult(*args, per_test_timeout=timeout, **kwargs),
    )
    result = runner.run(suite)
    return result.wasSuccessful()


def main(argv: list[str] | None = None) -> int:
    if not faulthandler.is_enabled():
        faulthandler.enable()

    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Limpa prefixos comuns de CI se passados
    if raw_argv and raw_argv[0] == "-m":
        raw_argv.pop(0)
    if raw_argv and raw_argv[0] == "unittest":
        raw_argv.pop(0)

    parser = argparse.ArgumentParser(description="Runner de testes com faulthandler e quarentena", add_help=False)
    parser.add_argument("--mode", choices=["blocking", "quarantine"], default="blocking")
    parser.add_argument("--os", default=os.environ.get("RUNNER_OS", sys.platform))
    parser.add_argument("--python", default=f"{sys.version_info.major}.{sys.version_info.minor}")
    parser.add_argument("--perfil", default=os.environ.get("T11_PERFIL", "full"))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--quarantine-file", type=Path, default=None)
    parser.add_argument("-v", "--verbose", action="store_true")

    args, test_args = parser.parse_known_args(raw_argv)

    current_cell = {
        "os": args.os,
        "python": _norm_python_version(args.python),
        "perfil": args.perfil,
    }

    # Carrega e valida arquivo de quarentena
    quarantine_path = args.quarantine_file
    if quarantine_path is None:
        # Tenta caminhos comuns
        for cand in (ROOT / "ci" / "quarentena.json", Path("ci/quarentena.json"), Path("quarentena.json")):
            if cand.is_file():
                quarantine_path = cand
                break
    quarantine_entries = load_and_validate_quarantine(quarantine_path) if quarantine_path else []

    # Tratamento especial para "tests/test_release_t11.py --core"
    if any("--core" in a for a in test_args):
        if args.mode == "quarantine":
            print("Nenhum teste em quarentena para o perfil core.")
            return 0
        # Executa o runner do release t11 core diretamente com faulthandler ativo
        cmd = [sys.executable, "-I", "-X", "utf8"] + test_args
        res = subprocess.run(cmd)
        return res.returncode

    # Monta suíte via unittest loader
    loader = unittest.defaultTestLoader
    if not test_args or test_args == ["discover"] or ("-s" in test_args and "discover" in test_args) or test_args[0] == "discover":
        start_dir = "tests"
        pattern = "test*.py"
        top_dir = None
        # Parse discover args
        disc_parser = argparse.ArgumentParser(add_help=False)
        disc_parser.add_argument("discover", nargs="?", default="discover")
        disc_parser.add_argument("-s", "--start-directory", default="tests")
        disc_parser.add_argument("-p", "--pattern", default="test*.py")
        disc_parser.add_argument("-t", "--top-level-directory", default=None)
        disc_parser.add_argument("-v", "--verbose", action="store_true")
        parsed_disc, remaining = disc_parser.parse_known_args(test_args)
        start_dir = parsed_disc.start_directory
        pattern = parsed_disc.pattern
        top_dir = parsed_disc.top_level_directory
        full_suite = loader.discover(start_dir, pattern=pattern, top_level_dir=top_dir)
    else:
        # Lista de nomes de módulos ou testes específicos
        full_suite = unittest.TestSuite()
        for name in test_args:
            if name.startswith("-"):
                continue
            if name.endswith(".py"):
                # Caminho de arquivo
                p = Path(name)
                parts = list(p.with_suffix("").parts)
                mod_path = ".".join(parts)
                try:
                    full_suite.addTests(loader.loadTestsFromName(mod_path))
                except (ImportError, AttributeError):
                    full_suite.addTests(loader.loadTestsFromName(p.stem))
            else:
                full_suite.addTests(loader.loadTestsFromName(name))

    # Filtra de acordo com o modo
    if args.mode == "blocking":
        filtered = filter_suite(
            full_suite,
            lambda t: not is_test_quarantined(t.id(), current_cell, quarantine_entries),
        )
    else:  # quarantine
        filtered = filter_suite(
            full_suite,
            lambda t: is_test_quarantined(t.id(), current_cell, quarantine_entries),
        )
        if filtered.countTestCases() == 0:
            print(f"Quarentena: nenhum teste configurado para a célula {current_cell}.")
            return 0

    print(
        f"Executando {filtered.countTestCases()} testes (modo: {args.mode}, célula: {current_cell}, timeout: {args.timeout}s)..."
    )
    success = run_tests_with_runner(
        filtered,
        timeout=args.timeout,
        verbosity=2 if args.verbose else 1,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
