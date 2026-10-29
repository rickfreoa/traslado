# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Audita bibliotecas nativas carregadas durante uma conversão Full do Traslado 2.0.0.

Registra e confronta contra ci/libs-permitidas-<so>.txt:
- Linux: ldd no Python gerenciado e nas extensões (_tkinter, PyMuPDF, Pillow, lxml) + /proc/<pid>/maps
- macOS: otool -L no Python gerenciado e nas extensões + DYLD_PRINT_LIBRARIES / vmmap
- Windows: módulos carregados no processo durante a conversão (EnumProcessModules / Get-Process -Module)
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _load_rules(allowlist_path: Path, managed_root: Path) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    forbidden: list[str] = []
    roots = {str(managed_root), str(managed_root.resolve())}
    if os.name == "nt":
        roots = {r.replace("/", "\\").rstrip("\\") + "\\" for r in roots}
    else:
        roots = {r.rstrip("/") + "/" for r in roots}

    for raw in allowlist_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            rule = line[1:].strip()
            forbidden.append(rule)
        elif "<ROOT>" in line:
            for r in sorted(roots):
                allowed.append(line.replace("<ROOT>/", r).replace("<ROOT>\\", r).replace("<ROOT>", r.rstrip("/\\")))
        else:
            allowed.append(line)
    return allowed, forbidden


def _is_permitted(lib_path: str, allowed: list[str], forbidden: list[str], is_windows: bool) -> tuple[bool, str]:
    cand = lib_path.strip()
    if not cand:
        return True, "empty"
    cand_cmp = cand.replace("/", "\\").lower() if is_windows else cand
    for f in forbidden:
        f_cmp = f.replace("/", "\\").lower() if is_windows else f
        if f_cmp in cand_cmp or cand_cmp.startswith(f_cmp):
            return False, f"bloqueado pela regra '!{f}'"
    for a in allowed:
        a_cmp = a.replace("/", "\\").lower() if is_windows else a
        if cand_cmp.startswith(a_cmp):
            return True, f"permitido por '{a}'"
    return False, "fora da allowlist"


# Bibliotecas que só podem vir da raiz gerenciada: a mesma biblioteca do sistema indicaria dependência oculta.
_PROIBIDAS_NO_SISTEMA = re.compile(r"^lib(python\d|tk\d|tcl\d|tesseract|lept|mupdf)", re.I)
GRUPOS_OBRIGATORIOS = ("_tkinter", "pymupdf", "pillow", "lxml")


def _pacotes_iniciais(caminho: Path) -> set[str]:
    """Nomes de pacote do inventário inicial do contêiner (dpkg-query -W ou rpm -qa)."""
    nomes: set[str] = set()
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in linha.split("\t")[0] or not linha.strip():
            continue  # linhas do /etc/os-release
        if "\t" in linha:  # dpkg-query -W: nome[:arq]<TAB>versão
            nomes.add(linha.split("\t", 1)[0].split(":", 1)[0])
        else:  # rpm -qa: nome-versão-release.arq
            nomes.add(linha.strip())
    return nomes


def _dono_do_pacote(lib: str) -> list[str]:
    """Pacotes donos de lib (dpkg -S ou rpm -qf), tentando também o caminho canônico (usrmerge)."""
    candidatos = [lib]
    real = os.path.realpath(lib)
    if real != lib:
        candidatos.append(real)
    for c in list(candidatos):
        for de, para in (("/usr/lib/", "/lib/"), ("/lib/", "/usr/lib/"), ("/usr/lib64/", "/lib64/"), ("/lib64/", "/usr/lib64/")):
            if c.startswith(de):
                candidatos.append(para + c[len(de):])
    for c in candidatos:
        if shutil.which("dpkg"):
            proc = subprocess.run(["dpkg", "-S", c], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                return sorted({ln.split(":", 1)[0].strip().split(",")[0].strip() for ln in proc.stdout.splitlines() if ":" in ln})
        elif shutil.which("rpm"):
            proc = subprocess.run(["rpm", "-qf", c], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                return sorted({ln.strip() for ln in proc.stdout.splitlines() if ln.strip()})
    return []


def _violacoes_de_sistema(observed: list[str], managed_root: Path, inventario: Path | None) -> list[dict[str, str]]:
    """E4: biblioteca de sistema precisa pertencer a pacote presente antes da instalação e não pode ser
    uma das bibliotecas que o Traslado traz consigo (python, tk/tcl, tesseract, leptonica, mupdf)."""
    raizes = {str(managed_root).rstrip("/") + "/", str(managed_root.resolve()).rstrip("/") + "/"}
    iniciais = _pacotes_iniciais(inventario) if inventario else None
    violacoes = []
    for lib in observed:
        if any(lib.startswith(r) for r in raizes) or not lib.startswith("/") or lib == "/etc/ld.so.cache":
            continue
        nome = Path(lib).name
        if _PROIBIDAS_NO_SISTEMA.match(nome):
            violacoes.append({"library": lib, "reason": "biblioteca que deveria vir da raiz gerenciada carregada do sistema"})
            continue
        if iniciais is None:
            continue
        donos = _dono_do_pacote(lib)
        if not donos:
            violacoes.append({"library": lib, "reason": "biblioteca de sistema sem pacote dono"})
        elif not any(d in iniciais or d.split(":", 1)[0] in iniciais for d in donos):
            violacoes.append({"library": lib, "reason": "pacote dono ausente do inventário inicial: " + ", ".join(donos)})
    return violacoes


def _find_extension_binaries(managed_root: Path) -> dict[str, list[Path]]:
    targets = {
        "python": [Path(sys.executable), Path(sys.executable).resolve()],
        "_tkinter": [],
        "pymupdf": [],
        "pillow": [],
        "lxml": [],
    }
    exts = (".so", ".dylib", ".pyd", ".dll")
    for base in (managed_root / "python", managed_root / "releases"):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            name_low = p.name.lower()
            if not any(name_low.endswith(ext) or ".so." in name_low for ext in exts):
                continue
            rel_low = p.as_posix().lower()
            if "_tkinter" in name_low or "libtk" in name_low or "libtcl" in name_low:
                targets["_tkinter"].append(p)
            elif "fitz" in rel_low or "pymupdf" in rel_low or "mupdf" in name_low:
                targets["pymupdf"].append(p)
            elif "/pil/" in rel_low or "pillow" in rel_low:
                targets["pillow"].append(p)
            elif "/lxml/" in rel_low:
                targets["lxml"].append(p)
    return {k: sorted({x.resolve() for x in v if x.exists()}) for k, v in targets.items()}


def _run_full_conversion_in_process() -> dict:
    import fitz  # noqa: F401
    import lxml.etree  # noqa: F401
    import PIL.Image  # noqa: F401
    import docx  # noqa: F401
    import openpyxl  # noqa: F401
    import pptx  # noqa: F401

    tkinter_loaded = False
    tkinter_error = None
    try:
        import _tkinter  # noqa: F401
        tkinter_loaded = True
    except Exception as exc:
        tkinter_error = f"{type(exc).__name__}: {exc}"

    from traslado.model import Options, Document
    from traslado.formats import extract
    from traslado.runner import execute
    import traslado

    pkg_assets = Path(traslado.__file__).resolve().parent / "assets"
    docx_fixture = pkg_assets / "fixture.docx"
    pdf_fixture = pkg_assets / "fixture.pdf"
    results = {}
    with tempfile.TemporaryDirectory(prefix="traslado-audit-conv-") as tmp:
        out_dir = Path(tmp) / "out"
        opts = Options(mode="native", chunks=True)
        for fx in (docx_fixture, pdf_fixture):
            if fx.is_file():
                # Exercise the actual parser in this process so maps/modules describe
                # conversion loads, rather than merely the supervisor imports.
                parser_dir = Path(tmp) / (fx.suffix[1:] + "-parser")
                parser_dir.mkdir()
                document = Document(fx.name, "audit")
                extract(fx, parser_dir, opts, document)
                res = execute(fx, out_dir / fx.stem, opts)
                results[fx.name] = {"status": res.get("status"), "error_code": res.get("error_code"),
                                    "warning_summary": res.get("warning_summary", {})}
                output = Path(res.get("output") or "missing")
                expected = f"Traslado verificacao de instalacao {fx.suffix[1:].upper()} OK."
                if not (output / "document.md").is_file() or expected not in (output / "document.md").read_text(encoding="utf-8"):
                    raise RuntimeError(f"Fixture sem sentinela esperada: {fx.name}: {res}")
    return {
        "tkinter_loaded": tkinter_loaded,
        "tkinter_error": tkinter_error,
        "conversions": results,
    }


def _audit_linux(managed_root: Path) -> tuple[list[str], dict]:
    binaries = _find_extension_binaries(managed_root)
    ldd_records: dict[str, list[str]] = {}
    missing_optional: dict[str, list[str]] = {}
    observed_paths: set[str] = set()

    for group, paths in binaries.items():
        for bpath in paths:
            proc = subprocess.run(["ldd", str(bpath)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
            ldd_records[str(bpath)] = lines
            for ln in lines:
                if "not found" in ln:
                    missing_optional.setdefault(str(bpath), []).append(ln)
                    continue
                m = re.search(r"=>\s*(\S+)\s+\(0x", ln)
                if m:
                    observed_paths.add(m.group(1))
                else:
                    m2 = re.match(r"^(/\S+)\s+\(0x", ln)
                    if m2:
                        observed_paths.add(m2.group(1))

    maps_paths: set[str] = set()
    maps_file = Path(f"/proc/{os.getpid()}/maps")
    if maps_file.is_file():
        for line in maps_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split(None, 5)
            if len(parts) == 6:
                mapped = parts[5].strip()
                if mapped.startswith("/"):
                    maps_paths.add(mapped.split(" (deleted)")[0])
    observed_paths |= maps_paths
    sem_binario = [g for g in GRUPOS_OBRIGATORIOS if not binaries.get(g)]
    if sem_binario:
        raise RuntimeError("Grupos sem binário inspecionado: " + ", ".join(sem_binario))
    return sorted(observed_paths), {
        "method": "ldd + /proc/<pid>/maps",
        "binaries_inspected": {k: [str(x) for x in v] for k, v in binaries.items()},
        "ldd": ldd_records,
        "ldd_not_found": missing_optional,
        "proc_maps_files": sorted(maps_paths),
    }


def _audit_macos(managed_root: Path) -> tuple[list[str], dict]:
    binaries = _find_extension_binaries(managed_root)
    otool_records: dict[str, list[str]] = {}
    observed_paths: set[str] = set()

    for group, paths in binaries.items():
        for bpath in paths:
            proc = subprocess.run(["otool", "-L", str(bpath)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            lines = [ln.strip() for ln in (proc.stdout or "").splitlines()[1:] if ln.strip()]
            otool_records[str(bpath)] = lines
            # Numa dylib, otool -L lista também o próprio install name (LC_ID_DYLIB), que não é dependência;
            # o delocate do Pillow usa o prefixo fictício /DLC/ nesse campo.
            ident = subprocess.run(["otool", "-D", str(bpath)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            proprio = {ln.strip() for ln in (ident.stdout or "").splitlines()[1:] if ln.strip()}
            for ln in lines:
                lib = ln.split(" (compatibility version")[0].strip()
                if lib and lib not in proprio:
                    observed_paths.add(lib)

    # Captura bibliotecas efetivamente carregadas em runtime via DYLD_PRINT_LIBRARIES
    env = os.environ.copy()
    env["DYLD_PRINT_LIBRARIES"] = "1"
    code = (
        "import fitz, lxml.etree, PIL.Image, docx, openpyxl, pptx\n"
        "try:\n import _tkinter\nexcept Exception:\n pass\n"
        "from pathlib import Path\n"
        "import tempfile, traslado\n"
        "from traslado.runner import execute\n"
        "from traslado.model import Options\n"
        "assets = Path(traslado.__file__).resolve().parent / 'assets'\n"
        "with tempfile.TemporaryDirectory() as t:\n"
        " execute(assets / 'fixture.docx', Path(t) / 'd', Options(mode='native'))\n"
        " execute(assets / 'fixture.pdf', Path(t) / 'p', Options(mode='native'))\n"
    )
    dyld_proc = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if dyld_proc.returncode != 0:
        raise RuntimeError(f"Conversão DYLD falhou: {dyld_proc.stderr}")
    dyld_libs: list[str] = []
    for ln in (dyld_proc.stderr or "").splitlines():
        if ln.startswith("dyld") and ":" in ln:
            candidate = "/" + ln.split("/", 1)[1] if "/" in ln else ""
            if candidate.startswith("/"):
                dyld_libs.append(candidate)
                observed_paths.add(candidate)

    vmmap_libs: list[str] = []
    vmmap_unresolved: list[str] = []
    if shutil.which("vmmap"):
        vm_proc = subprocess.run(["vmmap", "-w", str(os.getpid())], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        # Caminhos podem conter espaços ("Application Support"); a captura começa numa raiz absoluta conhecida
        # e vai até a extensão. Caminho que não existe no disco não é prova de carga: fica registrado à parte,
        # e a cobertura vem do DYLD_PRINT_LIBRARIES, que imprime o caminho completo.
        padrao = re.compile(r"(?:^|\s)(/(?:private|System|usr|Library|Users|opt|Applications|var|tmp)/.*?"
                            r"(?:\.dylib|\.so|\.framework/\S+))(?:\s|$)")
        for ln in (vm_proc.stdout or "").splitlines():
            m = padrao.search(ln)
            if m:
                caminho = m.group(1)
                if Path(caminho).exists():
                    vmmap_libs.append(caminho)
                    observed_paths.add(caminho)
                else:
                    vmmap_unresolved.append(ln.strip()[:300])

    if not dyld_libs and not vmmap_libs:
        raise RuntimeError("DYLD/vmmap não observaram bibliotecas runtime")
    sem_binario = [g for g in GRUPOS_OBRIGATORIOS if not binaries.get(g)]
    if sem_binario:
        raise RuntimeError("Grupos sem binário inspecionado: " + ", ".join(sem_binario))
    return sorted(observed_paths), {
        "method": "otool -L + DYLD_PRINT_LIBRARIES + vmmap",
        "binaries_inspected": {k: [str(x) for x in v] for k, v in binaries.items()},
        "otool": otool_records,
        "dyld_loaded": sorted(set(dyld_libs)),
        "vmmap_loaded": sorted(set(vmmap_libs)),
        "vmmap_unresolved": sorted(set(vmmap_unresolved))[:200],
    }


def _audit_windows(managed_root: Path) -> tuple[list[str], dict]:
    modules: set[str] = set()
    # 1. EnumProcessModules / GetModuleFileNameExW via ctypes
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        h_proc = kernel32.GetCurrentProcess()
        h_mods = (wintypes.HMODULE * 1024)()
        cb_needed = wintypes.DWORD()
        if psapi.EnumProcessModules(h_proc, ctypes.byref(h_mods), ctypes.sizeof(h_mods), ctypes.byref(cb_needed)):
            count = min(1024, cb_needed.value // ctypes.sizeof(wintypes.HMODULE))
            buf = ctypes.create_unicode_buffer(1024)
            for i in range(count):
                if psapi.GetModuleFileNameExW(h_proc, h_mods[i], buf, 1024):
                    modules.add(buf.value)
        else:
            raise OSError(ctypes.get_last_error(), "EnumProcessModules falhou")
    except Exception as exc:
        raise RuntimeError(f"Enumeração de módulos (EnumProcessModules) falhou: {exc}") from exc

    # 2. Complemento via PowerShell Get-Process -Id <pid> -Module
    ps_modules: list[str] = []
    try:
        ps_cmd = f"(Get-Process -Id {os.getpid()}).Modules | ForEach-Object {{ $_.FileName }}"
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-500:])
        for ln in (proc.stdout or "").splitlines():
            p = ln.strip()
            if p:
                ps_modules.append(p)
                modules.add(p)
    except Exception as exc:
        raise RuntimeError(f"Enumeração de módulos (Get-Process -Module) falhou: {exc}") from exc
    if not ps_modules:
        raise RuntimeError("Get-Process -Module não listou módulos")
    # Os modulos dos dois métodos devem incluir o próprio interpretador e as extensões do Full.
    nomes = {Path(m).name.lower() for m in modules}
    for exigido in ("_tkinter.pyd",):
        if exigido not in nomes:
            raise RuntimeError(f"Módulo esperado não carregado: {exigido}")

    if not modules:
        raise RuntimeError("Nenhum módulo Windows enumerado: auditoria indisponível")
    return sorted(modules), {
        "method": "EnumProcessModules + Get-Process -Module",
        "module_count": len(modules),
        "powershell_modules": sorted(set(ps_modules)),
    }


def auditar(managed_root: Path, allowlist_path: Path, inventario_inicial: Path | None = None) -> dict:
    conv_info = _run_full_conversion_in_process()
    if not conv_info["tkinter_loaded"]:
        raise RuntimeError("_tkinter não carregou no Python gerenciado: " + str(conv_info["tkinter_error"]))
    is_win = os.name == "nt"
    if is_win:
        observed, details = _audit_windows(managed_root)
    elif sys.platform == "darwin":
        observed, details = _audit_macos(managed_root)
    else:
        observed, details = _audit_linux(managed_root)

    if not observed:
        raise RuntimeError("Nenhuma biblioteca observada; auditoria não comprovada")
    if len(conv_info['conversions']) != 2 or any(
            r['status'] not in {'complete', 'partial'} or r['error_code'] is not None or
            (r['status'] == 'partial' and r['warning_summary'] != {'docx_formatting_partial': 1})
            for r in conv_info['conversions'].values()):
        raise RuntimeError(f"Conversões da auditoria falharam: {conv_info}")
    allowed, forbidden = _load_rules(allowlist_path, managed_root)
    violations: list[dict[str, str]] = []
    permitted: list[dict[str, str]] = []
    for lib in observed:
        ok, reason = _is_permitted(lib, allowed, forbidden, is_win)
        entry = {"library": lib, "reason": reason}
        if ok:
            permitted.append(entry)
        else:
            violations.append(entry)
    for binario, linhas in details.get("ldd_not_found", {}).items():
        for linha in linhas:
            violations.append({"library": linha, "reason": "dependência não resolvida (ldd not found) de " + binario})
    if not is_win and sys.platform != "darwin":
        violations += _violacoes_de_sistema(observed, managed_root, inventario_inicial)
        details["inventario_inicial"] = str(inventario_inicial) if inventario_inicial else None

    return {
        "platform": sys.platform,
        "python_executable": sys.executable,
        "managed_root": str(managed_root.resolve()),
        "allowlist_file": str(allowlist_path),
        "conversions": conv_info,
        "total_observed": len(observed),
        "permitted_count": len(permitted),
        "violation_count": len(violations),
        "violations": violations,
        "permitted": permitted,
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, required=True, help="Raiz gerenciada da instalação do Traslado")
    ap.add_argument("--allowlist", type=Path, required=True, help="Arquivo ci/libs-permitidas-<so>.txt")
    ap.add_argument("--saida", type=Path, required=True, help="Caminho do relatório JSON de auditoria")
    ap.add_argument("--inventario-inicial", type=Path, default=None,
                    help="Linux: inventário de pacotes anterior à instalação (dpkg-query -W ou rpm -qa); obrigatório no E2E")
    args = ap.parse_args(argv)

    try:
        if args.inventario_inicial is not None and not args.inventario_inicial.is_file():
            raise RuntimeError(f"Inventário inicial ausente: {args.inventario_inicial}")
        report = auditar(args.root, args.allowlist, args.inventario_inicial)
    except Exception as exc:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text(json.dumps({"success": False, "error": str(exc), "platform": sys.platform}, indent=2) + "\n", encoding="utf-8")
        raise
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if report["violations"]:
        for v in report["violations"]:
            print(f"VIOLACAO DE BIBLIOTECA: {v['library']} ({v['reason']})", file=sys.stderr)
        return 1
    print(f"Auditoria de bibliotecas aprovada ({report['total_observed']} bibliotecas verificadas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
