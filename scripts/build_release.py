"""Monta o pacote de instalação do Traslado: wheel próprio, INSTALL-PAYLOAD.json e ZIP com SHA256SUMS.

Uso (na raiz do repositório, Python 3.11-3.14): python scripts/build_release.py
Os binários OCR não entram no pacote: o instalador baixa os artefatos fixados em ocr-assets.lock.json
e confere tamanho e SHA-256 antes de usá-los.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install_windows import PRODUCT, VERSION  # noqa: E402  mesma versão que o instalador exige

FORA = {".git", ".github", "build", "ocr-cache", "__pycache__", ".pytest_cache", "work"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    dist = ROOT / "dist"
    for velho in dist.glob("*.whl"):
        velho.unlink()
    subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "-w", str(dist)], check=True)
    wheels = list(dist.glob(f"freoa_md_converter-{VERSION}-py3-none-any.whl"))
    assert len(wheels) == 1, wheels
    arquivos = sorted(p for p in ROOT.rglob("*") if p.is_file()
                      and not FORA.intersection(p.relative_to(ROOT).parts)
                      and p.suffix not in {".pyc", ".zip"} and p.name not in {"INSTALL-PAYLOAD.json", "SHA256SUMS"}
                      and not (p.parent == ROOT and p.name == ".gitignore"))
    payload = {"schema_version": 1, "product": PRODUCT, "version": VERSION,
               "files": [{"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size}
                         for p in arquivos]}
    (ROOT / "INSTALL-PAYLOAD.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    nome = f"traslado-{VERSION}-windows"
    zipado = dist / f"{nome}.zip"
    with zipfile.ZipFile(zipado, "w", zipfile.ZIP_DEFLATED) as z:
        for p in arquivos + [ROOT / "INSTALL-PAYLOAD.json"]:
            z.write(p, f"{nome}/{p.relative_to(ROOT).as_posix()}")
    soma = "".join(f"{sha(p)}  {p.name}\n" for p in (zipado, wheels[0]))
    (dist / "SHA256SUMS").write_text(soma, encoding="utf-8")
    print(soma, end="")


if __name__ == "__main__":
    main()
