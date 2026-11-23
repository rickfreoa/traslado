# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""OCR da instalação gerenciada: lock empacotado, bootstrap do Windows, tessdata e arquitetura."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

from .. import revocation
from ._common import POWERSHELL_WIN, normalize_languages

# Raiz do pacote `traslado`: os assets empacotados ficam em traslado/assets.
_PACOTE = Path(__file__).resolve().parent.parent

# Família, comando do pacote base e nome do pacote de cada idioma ({} = código Tesseract).
# O pacote base dessas distribuições traz, no máximo, inglês; sem os pacotes de idioma, o OCR
# do Traslado (que exige todos os idiomas pedidos) falha em todas as páginas escaneadas.
# openSUSE: nome dos subpacotes de idioma não conferido; a dica nomeia os idiomas sem inventar pacote.
_TESSERACT_LINUX = (
    ({"debian", "ubuntu", "linuxmint", "pop"}, "sudo apt install tesseract-ocr", "tesseract-ocr-{}"),
    ({"fedora", "rhel", "centos", "rocky", "almalinux"}, "sudo dnf install tesseract", "tesseract-langpack-{}"),
    ({"opensuse", "suse", "opensuse-leap", "opensuse-tumbleweed", "sles"}, "sudo zypper install tesseract-ocr", None),
    ({"arch", "manjaro", "endeavouros"}, "sudo pacman -S tesseract", "tesseract-data-{}"),
    ({"alpine"}, "sudo apk add tesseract-ocr", "tesseract-ocr-data-{}"),
)


def linux_tesseract_hint(os_release: str = "/etc/os-release", languages: str | None = None) -> str:
    """Comando de instalação do Tesseract para a distribuição em /etc/os-release (ID e ID_LIKE).

    Com `languages` ("por+eng"), inclui os pacotes desses idiomas: fora da instalação gerenciada,
    o OCR usa o tessdata do sistema, e o pacote base não os traz.
    """
    codes = [c for c in (languages or "").split("+") if c]
    campos: dict[str, list[str]] = {"ID": [], "ID_LIKE": []}
    try:
        with open(os_release, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key in campos:
                    campos[key] = value.strip().strip("\"'").lower().split()
    except OSError:
        pass
    for ident in campos["ID"] + campos["ID_LIKE"]:  # ID decide antes de ID_LIKE
        for family, command, package in _TESSERACT_LINUX:
            if ident in family:
                if codes and package:
                    # Debian e Ubuntu grafam com hífen os códigos compostos (chi_sim -> tesseract-ocr-chi-sim).
                    debian = package.startswith("tesseract-ocr-") and "apt" in command
                    command += "".join(" " + package.format(c.replace("_", "-") if debian else c) for c in codes)
                elif codes:
                    command += " e os modelos de idioma " + ", ".join(codes)
                return "Para instalar o Tesseract nesta distribuição, execute: " + command
    if codes:
        return ("Instale o pacote do Tesseract OCR e os modelos de idioma " + ", ".join(codes)
                + " pelo gerenciador de pacotes da sua distribuição.")
    return "Instale o pacote do Tesseract OCR pelo gerenciador de pacotes da sua distribuição."


def _find_ocr_lock(release_dir: Path, from_dir: Path | None = None) -> Path:
    candidate = _PACOTE / "assets/ocr-assets.lock.json"
    if not candidate.is_file():
        raise FileNotFoundError("Lock OCR empacotado ausente")
    return candidate


def _find_bootstrap_ps1(release_dir: Path, from_dir: Path | None = None) -> Path:
    candidate = _PACOTE / "assets/bootstrap_ocr.ps1"
    if not candidate.is_file():
        raise FileNotFoundError("Bootstrap OCR empacotado ausente")
    return candidate


def _install_tessdata_from_lock(
    destination_ocr: Path,
    languages: list[str],
    lock_path: Path,
    *,
    cache_dir: Path | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> Path:
    lock = json.loads(lock_path.read_text(encoding="utf-8-sig"))
    assets_by_name = {a["name"]: a for a in lock.get("assets", []) if isinstance(a, dict) and "name" in a}
    tessdata_dir = destination_ocr / "tessdata"
    doc_dir = destination_ocr / "doc"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    doc_dir.mkdir(parents=True, exist_ok=True)

    wanted = [*(f"{lang}.traineddata" for lang in languages), "TESSDATA-LICENSE.txt"]
    for name in wanted:
        spec = assets_by_name.get(name)
        if not spec:
            raise ValueError(f"Asset OCR ausente no lock: {name}")
        data: bytes | None = None
        if cache_dir and (cache_dir / name).is_file():
            data = (cache_dir / name).read_bytes()
        else:
            declared = int(spec["bytes"])
            if declared <= 0 or declared > 64 * 1024 * 1024:
                raise ValueError(f"Tamanho OCR fora do limite: {name}")
            limit = min(64 * 1024 * 1024, (declared * 101 + 99) // 100)
            data = fetch_bytes(spec["url"]) if fetch_bytes else revocation.default_fetch_bytes(spec["url"], max_bytes=limit)
            if cache_dir:
                cache_dir.mkdir(parents=True, exist_ok=True)
                (cache_dir / name).write_bytes(data)
        if len(data) != int(spec["bytes"]):
            raise ValueError(f"Tamanho divergente para {name}: {len(data)} != {spec['bytes']}")
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != spec["sha256"].lower():
            raise ValueError(f"SHA-256 divergente para {name}: {actual_sha} != {spec['sha256']}")
        target = (doc_dir / name) if name == "TESSDATA-LICENSE.txt" else (tessdata_dir / name)
        target.write_bytes(data)
    return tessdata_dir


def _purge_seven_zip(root: Path) -> None:
    """Garante que nenhum executável, DLL ou instalador do 7-Zip permaneça na instalação."""
    if not root.exists():
        return
    forbidden_names = {"7z.exe", "7z.dll", "7zr.exe", "7z2603-x64.exe"}
    for item in list(root.rglob("*")):
        try:
            if item.is_dir() and item.name in {".bootstrap-tools", ".ocr-download-cache"}:
                shutil.rmtree(item, ignore_errors=True)
            elif item.is_file() and item.name.lower() in forbidden_names:
                item.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_tesseract_version(raw_text: str) -> str | None:
    match = re.search(r"tesseract\s+v?([0-9]+(?:\.[0-9]+)*)", raw_text, re.I)
    return match.group(1) if match else None


def _get_windows_native_machine() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if hasattr(kernel32, "IsWow64Process2"):
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.IsWow64Process2.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ushort),
                ctypes.POINTER(ctypes.c_ushort),
            ]
            proc_m = ctypes.c_ushort()
            nat_m = ctypes.c_ushort()
            if kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(proc_m), ctypes.byref(nat_m)):
                return nat_m.value
    except Exception:
        pass
    return None


def _read_pe_machine(path: Path) -> int:
    with open(path, "rb") as f:
        header = f.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise RuntimeError(f"OCR: executavel sem cabecalho MZ: {path}")
        pe_offset = int.from_bytes(header[0x3C:0x40], "little")
        f.seek(pe_offset)
        sig = f.read(24)
        if len(sig) < 24 or sig[:4] != b"PE\0\0":
            raise RuntimeError(f"OCR: assinatura PE ausente: {path}")
        return int.from_bytes(sig[4:6], "little")


def _assert_pe_architecture(path: Path, role: str = "tesseract") -> None:
    machine = _read_pe_machine(path)
    if machine == 0xAA64:
        raise RuntimeError(f"OCR: Windows ARM64 nao suportado nesta versao; executavel {role} e ARM64")
    if machine == 0x014C:
        raise RuntimeError(f"OCR: executavel {role} e x86 (32 bits); requer versao x64")
    if machine != 0x8664:
        raise RuntimeError(f"OCR: arquitetura de executavel {role} inesperada (0x{machine:04X}); requer x64 (0x8664)")


def _assert_platform_architecture(platform_name: str | None = None, env: dict[str, str] | None = None) -> None:
    plat = platform_name or sys.platform
    if plat != "win32":
        raise RuntimeError("OCR: requer Windows x64")
    environment = os.environ if env is None else env
    nat_m = _get_windows_native_machine()
    if nat_m == 0xAA64:
        raise RuntimeError("OCR: Windows ARM64 nao suportado nesta versao; sistema operacional e ARM64")
    if nat_m == 0x014C:
        raise RuntimeError("OCR: Windows x86 (32 bits) nao suportado nesta versao; requer Windows x64")
    if sys.maxsize <= 2**32:
        raise RuntimeError("OCR: Windows x86 (32 bits) nao suportado nesta versao; requer Windows x64")
    if nat_m == 0x8664:
        return
    env_arch = (environment.get("PROCESSOR_ARCHITEW6432") or environment.get("PROCESSOR_ARCHITECTURE", "")).upper()
    if "ARM" in env_arch:
        raise RuntimeError("OCR: Windows ARM64 nao suportado nesta versao; sistema operacional e ARM64")
    if env_arch == "X86" and not environment.get("PROCESSOR_ARCHITEW6432"):
        raise RuntimeError("OCR: Windows x86 (32 bits) nao suportado nesta versao; requer Windows x64")


def setup_ocr(
    release_dir: Path,
    *,
    root: Path | None = None,
    profile: str = "Full",
    languages: list[str] | None = None,
    skip_ocr: bool = False,
    from_dir: Path | None = None,
    ocr_cache: Path | None = None,
    platform_name: str | None = None,
    env: dict[str, str] | None = None,
    fetch_bytes: Callable[[str], bytes] | None = None,
) -> dict:
    platform_name = platform_name or sys.platform
    env = os.environ if env is None else env
    langs = normalize_languages(languages)
    if profile != "Full" or skip_ocr or env.get("TRASLADO_SKIP_OCR") == "1":
        return {
            "ocr_installed": False,
            "ocr": {"tesseract": None, "origin": "none", "version": None},
            "tessdata": None,
        }

    lock_path = _find_ocr_lock(release_dir, from_dir)
    cache = (
        Path(ocr_cache)
        if ocr_cache
        else Path(env["FREOA_OCR_TEST_CACHE"])
        if env.get("FREOA_OCR_TEST_CACHE")
        else (from_dir / "ocr-cache")
        if (from_dir and (from_dir / "ocr-cache").is_dir())
        else None
    )

    dest_ocr = release_dir / "ocr"
    if platform_name == "win32":
        if (dest_ocr / "tesseract.exe").is_file() and (dest_ocr / "tessdata").is_dir():
            _assert_platform_architecture(platform_name, env)
            _assert_pe_architecture(dest_ocr / "tesseract.exe", "tesseract")
            exe = str(dest_ocr / "tesseract.exe")
            version = "5.5.3"
            receipt_file = dest_ocr / "ocr-runtime.json"
            if receipt_file.is_file():
                try:
                    v = _parse_tesseract_version(json.loads(receipt_file.read_text(encoding="utf-8-sig")).get("version", ""))
                    if v:
                        version = v
                except Exception:
                    pass
            if root:
                _purge_seven_zip(root)
            _purge_seven_zip(release_dir)
            return {
                "ocr_installed": True,
                "ocr": {"tesseract": exe, "origin": "lock", "version": version},
                "tessdata": str(dest_ocr / "tessdata"),
            }

        ps1 = _find_bootstrap_ps1(release_dir, from_dir)
        temp_cache_ctx = tempfile.TemporaryDirectory(prefix="traslado-ocr-cache-") if cache is None else None
        try:
            effective_cache = cache if cache is not None else Path(temp_cache_ctx.name)
            cmd = [
                str(POWERSHELL_WIN if POWERSHELL_WIN.is_file() else "powershell.exe"),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                "-Destination",
                str(dest_ocr),
                "-CacheDirectory",
                str(effective_cache),
                "-LockPath",
                str(lock_path),
                "-Languages",
                ",".join(langs),
            ]
            if env.get("TRASLADO_OCR_OFFLINE") == "1":
                cmd.append("-Offline")
            proc = subprocess.run(
                cmd,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                raw_err = proc.stderr if proc.stderr is not None else b""
                if isinstance(raw_err, str):
                    raw_err = raw_err.encode("utf-8", errors="replace")
                tail_err = raw_err[-4096:].decode("utf-8", errors="replace").strip()
                detalhe = f": {tail_err}" if tail_err else ""
                raise RuntimeError(
                    f"Falha no bootstrap OCR (código {proc.returncode}){detalhe}"
                )
        finally:
            if temp_cache_ctx is not None:
                temp_cache_ctx.cleanup()
            if root:
                _purge_seven_zip(root)
            _purge_seven_zip(release_dir)

        exe = str(dest_ocr / "tesseract.exe")
        version = "5.5.3"
        receipt_file = dest_ocr / "ocr-runtime.json"
        if receipt_file.is_file():
            try:
                v = _parse_tesseract_version(json.loads(receipt_file.read_text(encoding="utf-8-sig")).get("version", ""))
                if v:
                    version = v
            except Exception:
                pass
        return {
            "ocr_installed": True,
            "ocr": {"tesseract": exe, "origin": "lock", "version": version},
            "tessdata": str(dest_ocr / "tessdata"),
        }

    # macOS (darwin) and Linux
    exe_path: str | None = None
    origin = "none"
    search_path = env.get("PATH", os.environ.get("PATH", ""))

    if platform_name == "darwin":
        brew = shutil.which("brew", path=search_path)
        if brew:
            try:
                proc = subprocess.run([brew, "--prefix", "tesseract"], capture_output=True, text=True, timeout=15)
                if proc.returncode == 0 and proc.stdout.strip():
                    candidate = Path(proc.stdout.strip()) / "bin/tesseract"
                    if candidate.is_file() and os.access(candidate, os.X_OK):
                        exe_path = str(candidate.resolve())
                        origin = "homebrew"
            except (OSError, subprocess.SubprocessError):
                pass
        if not exe_path:
            found = shutil.which("tesseract", path=search_path)
            if found:
                exe_path = str(Path(found).resolve())
                origin = "system"
        if not exe_path:
            print("OCR indisponível. Para instalar o Tesseract no macOS, execute: brew install tesseract")
            return {
                "ocr_installed": False,
                "ocr": {"tesseract": None, "origin": "none", "version": None},
                "tessdata": None,
            }
    else:
        found = shutil.which("tesseract", path=search_path)
        if found:
            exe_path = str(Path(found).resolve())
            origin = "system"
        else:
            print("OCR indisponível. " + linux_tesseract_hint())
            return {
                "ocr_installed": False,
                "ocr": {"tesseract": None, "origin": "none", "version": None},
                "tessdata": None,
            }

    tessdata_dir = _install_tessdata_from_lock(
        dest_ocr,
        langs,
        lock_path,
        cache_dir=cache,
        fetch_bytes=fetch_bytes,
    )
    version_str = None
    try:
        proc = subprocess.run([exe_path, "--version"], capture_output=True, text=True, timeout=10)
        version_str = _parse_tesseract_version((proc.stdout or "") + "\n" + (proc.stderr or ""))
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "ocr_installed": True,
        "ocr": {"tesseract": exe_path, "origin": origin, "version": version_str},
        "tessdata": str(tessdata_dir),
    }
