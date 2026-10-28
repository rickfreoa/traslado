# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Confere versão única entre traslado.__version__, scripts embutidos, wheel/sdist e tag Git."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tomllib
import zipfile

RAIZ = Path(__file__).resolve().parents[1]


def extrair_versao_init(raiz: Path = RAIZ) -> str:
    texto = (raiz / "src/traslado/__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"\s*$', texto, re.M)
    if not m:
        raise ValueError("src/traslado/__init__.py não define __version__")
    return m.group(1)


def conferir_versao(
    raiz: Path = RAIZ,
    dist: Path | None = None,
    tag: str | None = None,
) -> list[str]:
    erros: list[str] = []
    try:
        versao = extrair_versao_init(raiz)
    except Exception as exc:
        return [str(exc)]

    # 1. pyproject.toml usa versão dinâmica apontando para traslado.__version__
    pyproject = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
    proj = pyproject.get("project", {})
    if "version" in proj:
        erros.append("pyproject.toml declara project.version estático em vez de dynamic")
    if "version" not in proj.get("dynamic", []):
        erros.append("pyproject.toml não inclui 'version' em project.dynamic")
    dyn_ver = pyproject.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get("version")
    if dyn_ver != {"attr": "traslado.__version__"}:
        erros.append(f"pyproject.toml dynamic.version inesperado: {dyn_ver}")

    # 2. scripts/install_windows.py
    spec = importlib.util.spec_from_file_location("install_windows_versao", raiz / "scripts/install_windows.py")
    if spec is None or spec.loader is None:
        erros.append("scripts/install_windows.py não pôde ser carregado")
    else:
        instalador = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(instalador)
        if getattr(instalador, "VERSION", None) != versao:
            erros.append(f"scripts/install_windows.py VERSION={getattr(instalador, 'VERSION', None)} != {versao}")

    # 3. install.ps1 e install.sh
    ps1 = (raiz / "install.ps1").read_text(encoding="utf-8-sig")
    m_ps1 = re.search(r'\[string\]\$Version\s*=\s*"([^"]+)"', ps1)
    if not m_ps1 or m_ps1.group(1) != versao:
        erros.append(f"install.ps1 $Version={m_ps1.group(1) if m_ps1 else None} != {versao}")

    sh = (raiz / "install.sh").read_text(encoding="utf-8")
    m_sh = re.search(r'^VERSION="([^"]+)"\s*$', sh, re.M)
    if not m_sh or m_sh.group(1) != versao:
        erros.append(f"install.sh VERSION={m_sh.group(1) if m_sh else None} != {versao}")

    # 4. Cabeçalho de fonte correspondente nos wrappers e instaladores públicos
    tag_esperada = f"v{versao}"
    for nome_script in ("install.ps1", "install.sh", "INSTALAR.bat", "instalar.command", "instalar.sh"):
        caminho = raiz / nome_script
        if caminho.is_file():
            conteudo = caminho.read_text(encoding="utf-8-sig")
            m_tree = re.search(r"https://github\.com/rickfreoa/traslado/tree/(v\S+)", conteudo)
            if not m_tree or m_tree.group(1) != tag_esperada:
                erros.append(f"{nome_script}: tag de fonte correspondente {m_tree.group(1) if m_tree else None} != {tag_esperada}")

    # 5. Inventário e THIRD-PARTY.md
    inv_path = raiz / "THIRD-PARTY-INVENTORY.json"
    if inv_path.is_file():
        inv = json.loads(inv_path.read_text(encoding="utf-8-sig"))
        if inv.get("candidate_version") != versao:
            erros.append(f"THIRD-PARTY-INVENTORY.json candidate_version={inv.get('candidate_version')} != {versao}")

    # 6. Artefatos construídos em dist/ (se informado ou presente)
    if dist is not None and not dist.is_dir():
        erros.append(f"Diretório de artefatos ausente: {dist}")
    if dist is not None and dist.is_dir():
        whl = dist / f"traslado-{versao}-py3-none-any.whl"
        if not whl.is_file():
            erros.append(f"Wheel esperado ausente em {dist}: {whl.name}")
        else:
            with zipfile.ZipFile(whl) as z:
                meta_names = [n for n in z.namelist() if n.endswith(".dist-info/METADATA")]
                if not meta_names:
                    erros.append(f"{whl.name}: METADATA ausente")
                else:
                    meta = z.read(meta_names[0]).decode("utf-8")
                    m_ver = re.search(r"^Version:\s*(\S+)\s*$", meta, re.M)
                    if not m_ver or m_ver.group(1) != versao:
                        erros.append(f"{whl.name}: Version={m_ver.group(1) if m_ver else None} != {versao}")

        sdist = dist / f"traslado-{versao}.tar.gz"
        if not sdist.is_file():
            erros.append(f"sdist esperado ausente em {dist}: {sdist.name}")
        else:
            with tarfile.open(sdist, "r:gz") as tf:
                pkg_infos = [m for m in tf.getmembers() if m.isfile() and m.name.endswith("/PKG-INFO")]
                if not pkg_infos:
                    erros.append(f"{sdist.name}: PKG-INFO ausente")
                else:
                    ext = tf.extractfile(pkg_infos[0])
                    pkg_txt = ext.read().decode("utf-8") if ext is not None else ""
                    m_ver = re.search(r"^Version:\s*(\S+)\s*$", pkg_txt, re.M)
                    if not m_ver or m_ver.group(1) != versao:
                        erros.append(f"{sdist.name}: Version={m_ver.group(1) if m_ver else None} != {versao}")

        zip_win = dist / f"traslado-{versao}-windows.zip"
        if not zip_win.is_file():
            erros.append(f"ZIP esperado ausente em {dist}: {zip_win.name}")
        else:
            with zipfile.ZipFile(zip_win) as z:
                payload_name = f"traslado-{versao}-windows/INSTALL-PAYLOAD.json"
                if payload_name not in z.namelist():
                    erros.append(f"{zip_win.name}: {payload_name} ausente")
                else:
                    payload = json.loads(z.read(payload_name).decode("utf-8"))
                    if payload.get("version") != versao:
                        erros.append(f"{zip_win.name}: payload.version={payload.get('version')} != {versao}")

        dist_ps1 = dist / "install.ps1"
        if not dist_ps1.is_file():
            erros.append("dist/install.ps1 ausente")
        if dist_ps1.is_file():
            m_dps1 = re.search(r'\[string\]\$Version\s*=\s*"([^"]+)"', dist_ps1.read_text(encoding="utf-8-sig"))
            if not m_dps1 or m_dps1.group(1) != versao:
                erros.append(f"dist/install.ps1 $Version={m_dps1.group(1) if m_dps1 else None} != {versao}")

        dist_sh = dist / "install.sh"
        if not dist_sh.is_file():
            erros.append("dist/install.sh ausente")
        if dist_sh.is_file():
            m_dsh = re.search(r'^VERSION="([^"]+)"\s*$', dist_sh.read_text(encoding="utf-8"), re.M)
            if not m_dsh or m_dsh.group(1) != versao:
                erros.append(f"dist/install.sh VERSION={m_dsh.group(1) if m_dsh else None} != {versao}")

    # 7. Tag Git (quando fornecida ou em execução de tag no GitHub Actions)
    tag_efetiva = tag
    if not tag_efetiva and os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag_efetiva = os.environ.get("GITHUB_REF_NAME")
    if tag_efetiva:
        tag_limpa = tag_efetiva.removeprefix("refs/tags/")
        if tag_limpa != tag_esperada:
            erros.append(f"Tag Git '{tag_limpa}' diverge da versão '{tag_esperada}'")

    return erros


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--conferir", action="store_true", help="Confere consistência de versão em todo o repositório")
    ap.add_argument("--imprimir", action="store_true", help="Imprime apenas a versão corrente")
    ap.add_argument("--raiz", type=Path, default=RAIZ)
    ap.add_argument("--dist", type=Path, default=None, help="Diretório dist/ opcional para conferir artefatos gerados")
    ap.add_argument("--tag", type=str, default=None, help="Tag Git opcional (ex.: v2.0.0) para conferir")
    args = ap.parse_args(argv)

    versao = extrair_versao_init(args.raiz)
    if args.imprimir and not args.conferir:
        print(versao)
        return 0

    erros = conferir_versao(raiz=args.raiz, dist=args.dist, tag=args.tag)
    if erros:
        for e in erros:
            print("ERRO DE VERSAO:", e, file=sys.stderr)
        return 1
    print(f"Versão única conferida com sucesso: {versao}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
