# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Regenera e confere os locks universais por perfil em locks/ com uv pip compile."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCKS_DIR = ROOT / "locks"
PROFILES = ("core", "full", "legacy")
PYTHON_VERSION = "3.13"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_packages(text: str) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if match:
            packages[_norm(match.group(1))] = match.group(2)
    return packages


def count_hashes(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            current = _norm(match.group(1))
            counts[current] = 0
        if current:
            counts[current] += len(re.findall(r"--hash=sha256:[0-9a-f]{64}", line))
    return counts


def find_uv() -> str:
    uv = os.environ.get("TRASLADO_TEST_UV") or shutil.which("uv")
    if not uv:
        raise RuntimeError("uv não encontrado no PATH")
    return uv


def generate_locks(root: Path = ROOT) -> None:
    uv = find_uv()
    env = os.environ.copy()
    env["UV_NO_MANAGED_PYTHON"] = "1"
    for profile in PROFILES:
        in_rel = f"locks/{profile}.in"
        out_rel = f"locks/requirements-{profile}.hashes.lock"
        cmd = [
            uv,
            "pip",
            "compile",
            "--universal",
            "--generate-hashes",
            "--python-version",
            PYTHON_VERSION,
            in_rel,
            "-o",
            out_rel,
        ]
        subprocess.run(cmd, cwd=root, env=env, check=True)
        out_path = root / out_rel
        text = out_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        out_path.write_text(text, encoding="utf-8", newline="\n")


def verify_locks(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    locks_dir = root / "locks"
    for profile in PROFILES:
        in_file = locks_dir / f"{profile}.in"
        lock_file = locks_dir / f"requirements-{profile}.hashes.lock"
        if not in_file.is_file():
            errors.append(f"Entrada ausente: {in_file}")
            continue
        if not lock_file.is_file():
            errors.append(f"Lock ausente: {lock_file}")
            continue
        text = lock_file.read_text(encoding="utf-8")
        pkgs = parse_packages(text)
        hashes = count_hashes(text)
        for name in pkgs:
            if hashes.get(name, 0) < 1:
                errors.append(f"{lock_file.name}: pacote sem hash sha256: {name}")
        if profile == "core":
            if pkgs:
                errors.append(f"requirements-core.hashes.lock deve ser vazio, mas contém: {pkgs}")
        elif profile == "full":
            ref_path = root / "requirements-full.lock"
            if ref_path.is_file():
                expected = parse_packages(ref_path.read_text(encoding="utf-8"))
                if pkgs != expected:
                    errors.append(f"requirements-full.hashes.lock diverge de requirements-full.lock: {pkgs} != {expected}")
        elif profile == "legacy":
            if pkgs.get("firecrawl-anydoc") != "0.2.4":
                errors.append(f"requirements-legacy.hashes.lock deve fixar firecrawl-anydoc==0.2.4, mas contém: {pkgs}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", "--verificar", action="store_true", dest="check_only",
                        help="Apenas confere os locks existentes sem regenerar")
    args = parser.parse_args(argv)
    if not args.check_only:
        generate_locks(args.root)
    errors = verify_locks(args.root)
    if errors:
        for err in errors:
            print("ERRO:", err, file=sys.stderr)
        return 1
    print("Locks conferidos com sucesso.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
