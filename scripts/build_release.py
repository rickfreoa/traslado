"""Monta o pacote de instalação do Traslado: wheel próprio, INSTALL-PAYLOAD.json, ZIP determinístico e SHA256SUMS.

Uso (na raiz do repositório, Python 3.11-3.14):
    python scripts/build_release.py [--dist DIR] [--permitir-sujo]
    python scripts/build_release.py --verificar ARQUIVO...   # reprova se houver bytes de PyMuPDF/MuPDF

Só entram no ZIP os caminhos de release-files.txt (todos rastreados pelo git), o wheel construído e o
INSTALL-PAYLOAD.json. O build recusa árvore suja, salvo --permitir-sujo (só para teste local), caso em que o
payload registra "checkout_limpo": false. Os binários OCR não entram no pacote: o instalador baixa os
artefatos fixados em ocr-assets.lock.json e confere tamanho e SHA-256 antes de usá-los.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install_windows import PRODUCT, VERSION  # noqa: E402  mesma versão que o instalador exige

DATA_FIXA = (1980, 1, 1, 0, 0, 0)
# Nomes de membro que denunciam PyMuPDF/MuPDF (qualquer componente do caminho, sem diferenciar caixa).
NOME_MUPDF = re.compile(r"(?:^|[/\\._-])(?:fitz|pymupdf|mupdf|_mupdf)(?:$|[/\\._-])", re.I)
CONTEUDO_MUPDF = re.compile(rb"mupdf", re.I)
# Assinaturas executáveis: PE, ELF, Mach-O (32/64, ambas as ordens) e fat binary.
ASSINATURAS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def e_binario(dados: bytes) -> bool:
    """Independe do nome: assinatura executável, byte NUL ou bytes que não são UTF-8 válido."""
    if dados.startswith(ASSINATURAS) or b"\0" in dados:
        return True
    try:
        dados.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def sha(dados: bytes | Path) -> str:
    return hashlib.sha256(dados.read_bytes() if isinstance(dados, Path) else dados).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def listados() -> list[str]:
    nomes = [n.strip() for n in (ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines()
             if n.strip() and not n.startswith("#")]
    rastreados = set(git("ls-files", "-z").split("\0"))
    ausentes = [n for n in nomes if n not in rastreados]
    if ausentes or len(set(nomes)) != len(nomes):
        raise SystemExit(f"release-files.txt: caminho não rastreado pelo git ou repetido: {ausentes}")
    return sorted(nomes)


def checkout_limpo(nomes: list[str]) -> bool:
    return not git("status", "--porcelain").strip() and subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", *nomes]).returncode == 0


def conferir_wheel(wheel: Path, nomes: list[str]) -> None:
    """Os membros do wheel têm de ser exatamente o código autorizado em release-files.txt mais o dist-info."""
    esperado = {n.removeprefix("src/") for n in nomes if n.startswith("src/")}
    with zipfile.ZipFile(wheel) as z:
        membros = set(z.namelist())
    info = {m for m in membros if m.split("/")[0] == f"freoa_md_converter-{VERSION}.dist-info"}
    if membros - info != esperado:
        raise SystemExit(f"Wheel diverge da seleção autorizada: a mais {sorted(membros - info - esperado)}, "
                         f"a menos {sorted(esperado - membros)}")


def construir_wheel(destino: Path, nomes: list[str]) -> Path:
    """Compila só a partir dos arquivos autorizados (release-files.txt), copiados para um diretório novo."""
    with tempfile.TemporaryDirectory(prefix="t11-wheel-") as tmp:
        fonte = Path(tmp) / "fonte"
        for nome in nomes:
            (fonte / nome).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / nome, fonte / nome)
        env = {**os.environ, "SOURCE_DATE_EPOCH": git("log", "-1", "--format=%ct").strip()}
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(fonte), "--no-deps", "-w", str(destino)],
                       check=True, env=env)
    wheels = list(destino.glob(f"freoa_md_converter-{VERSION}-py3-none-any.whl"))
    assert len(wheels) == 1, wheels
    conferir_wheel(wheels[0], nomes)
    return wheels[0]


def membros_proibidos(nome: str, dados: bytes, caminho: str = "") -> list[str]:
    """Motivos pelos quais um membro (e, recursivamente, um zip/wheel aninhado) contém PyMuPDF/MuPDF."""
    achados = []
    if NOME_MUPDF.search(nome):
        achados.append(f"{caminho}{nome}: nome")
    if zipfile.is_zipfile(io.BytesIO(dados)):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for info in z.infolist():
                achados += membros_proibidos(info.filename, z.read(info), f"{caminho}{nome}!")
    elif e_binario(dados) and CONTEUDO_MUPDF.search(dados):  # texto UTF-8 válido pode citar MuPDF (ex.: THIRD-PARTY.md)
        achados.append(f"{caminho}{nome}: conteúdo")
    return achados


def verificar(arquivos: list[Path]) -> int:
    achados = [a for p in arquivos for a in membros_proibidos(p.name, p.read_bytes())]
    for a in achados:
        print("PyMuPDF/MuPDF presente:", a, file=sys.stderr)
    return 1 if achados else 0


def construir(dist: Path, permitir_sujo: bool = False) -> tuple[Path, Path]:
    nomes = listados()
    limpo = checkout_limpo(nomes)
    if not limpo and not permitir_sujo:
        raise SystemExit("Checkout sujo ou arquivo listado difere de HEAD: recusado (--permitir-sujo só para teste local).")
    dist.mkdir(parents=True, exist_ok=True)
    for velho in dist.glob("*.whl"):
        velho.unlink()
    wheel = construir_wheel(dist, nomes)
    itens = {n: (ROOT / n).read_bytes() for n in nomes}
    itens[f"dist/{wheel.name}"] = wheel.read_bytes()  # o instalador exige o wheel próprio no payload
    payload = {"schema_version": 1, "product": PRODUCT, "version": VERSION, "checkout_limpo": limpo,
               "commit": git("rev-parse", "HEAD").strip(),
               "files": [{"path": n, "sha256": sha(b), "bytes": len(b)} for n, b in sorted(itens.items())]}
    itens["INSTALL-PAYLOAD.json"] = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    # Cópia na raiz (ignorada pelo git): testes e instalador a usam quando a raiz atua como pacote.
    (ROOT / "INSTALL-PAYLOAD.json").write_bytes(itens["INSTALL-PAYLOAD.json"])
    nome = f"traslado-{VERSION}-windows"
    zipado = dist / f"{nome}.zip"
    with zipfile.ZipFile(zipado, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho, dados in sorted(itens.items()):
            info = zipfile.ZipInfo(f"{nome}/{caminho}", DATA_FIXA)
            info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 3, 0o100644 << 16
            z.writestr(info, dados)
    if verificar([zipado, wheel]):
        zipado.unlink()
        raise SystemExit("Artefato contém PyMuPDF/MuPDF: removido.")
    (dist / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.name}\n" for p in (zipado, wheel)),
                                     encoding="utf-8", newline="\n")
    return zipado, wheel


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dist", type=Path, default=ROOT / "dist")
    ap.add_argument("--permitir-sujo", action="store_true")
    ap.add_argument("--verificar", nargs="+", type=Path, metavar="ARQUIVO")
    args = ap.parse_args()
    if args.verificar:
        sys.exit(verificar(args.verificar))
    construir(args.dist, args.permitir_sujo)
    print((args.dist / "SHA256SUMS").read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()
