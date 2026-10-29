# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
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
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from install_windows import PRODUCT, VERSION  # noqa: E402  mesma versão que o instalador exige

DATA_FIXA = (1980, 1, 1, 0, 0, 0)
EPOCA_FIXA = 315532800  # 1980-01-01T00:00:00Z
# Nomes de membro que denunciam PyMuPDF/MuPDF (qualquer componente do caminho, sem diferenciar caixa).
NOME_MUPDF = re.compile(r"(?:^|[/\\._-])(?:fitz|pymupdf|mupdf|_mupdf)(?:$|[/\\._-])", re.I)
CONTEUDO_MUPDF = re.compile(rb"mupdf", re.I)
# Assinaturas executáveis: PE, ELF, Mach-O (32/64, ambas as ordens) e fat binary.
ASSINATURAS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


# Exigência declarada: todo membro com estas extensões (ou sem extensão, como LICENCE) é texto UTF-8, com ou sem BOM.
TEXTUAIS = {".py", ".ps1", ".bat", ".sh", ".command", ".in", ".md", ".txt", ".json", ".toml", ".lock", ".yml", ".yaml", ".cfg", ".ini", ""}


def utf8_valido(dados: bytes) -> bool:
    try:
        dados.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def e_binario(dados: bytes) -> bool:
    """Independe do nome: assinatura executável, byte NUL ou bytes que não são UTF-8 válido."""
    return dados.startswith(ASSINATURAS) or b"\0" in dados or not utf8_valido(dados)


def sha(dados: bytes | Path) -> str:
    return hashlib.sha256(dados.read_bytes() if isinstance(dados, Path) else dados).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def listados(permitir_nao_rastreados: bool = False) -> list[str]:
    linhas = [n.strip() for n in (ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines()
              if n.strip() and not n.startswith("#")]
    nomes = [n for n in linhas if not n.startswith("!")]
    excecoes = [n[1:] for n in linhas if n.startswith("!")]  # "!caminho" = módulo rastreado fora do pacote, por decisão revisável
    rastreados = set(git("ls-files", "-z").split("\0"))
    ausentes = [n for n in nomes + excecoes if n not in rastreados]
    if (ausentes and not permitir_nao_rastreados) or len(set(nomes)) != len(nomes) or set(nomes) & set(excecoes):
        raise SystemExit(f"release-files.txt: caminho não rastreado pelo git, repetido ou ao mesmo tempo listado e excluído: {ausentes}")
    esquecidos = sorted(n for n in rastreados if n.startswith("src/") and n.endswith(".py") and n not in nomes and n not in excecoes)
    if esquecidos:
        raise SystemExit(f"módulo rastreado fora de release-files.txt (liste-o ou declare '!caminho'): {esquecidos}")
    return sorted(nomes)


def checkout_limpo(nomes: list[str]) -> bool:
    return not git("status", "--porcelain").strip() and subprocess.run(
        ["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", *nomes]).returncode == 0


def conferir_wheel(wheel: Path, nomes: list[str]) -> None:
    """Os membros do wheel têm de ser exatamente o código autorizado em release-files.txt mais o dist-info."""
    esperado = {n.removeprefix("src/") for n in nomes if n.startswith("src/")}
    with zipfile.ZipFile(wheel) as z:
        membros = set(z.namelist())
    info = {m for m in membros if m.split("/")[0] == f"traslado-{VERSION}.dist-info"}
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
    wheels = list(destino.glob(f"traslado-{VERSION}-py3-none-any.whl"))
    assert len(wheels) == 1, wheels
    conferir_wheel(wheels[0], nomes)
    return wheels[0]


def construir_sdist(destino: Path, nomes: list[str], wheel: Path) -> Path:
    """Gera o sdist determinístico traslado-<VERSION>.tar.gz com código, testes, scripts e LICENCE."""
    rastreados = set(git("ls-files", "-z").split("\0"))
    testes = sorted(n for n in rastreados if n.startswith("tests/") and (ROOT / n).is_file())
    com_release_files = ["release-files.txt"] if "release-files.txt" in rastreados else []
    arquivos = sorted(set(nomes) | set(testes) | set(com_release_files))
    with zipfile.ZipFile(wheel) as z:
        pkg_info = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA")))

    prefixo = f"traslado-{VERSION}"
    sdist = destino / f"{prefixo}.tar.gz"
    entradas: list[tuple[str, bytes, int]] = [(f"{prefixo}/PKG-INFO", pkg_info, 0o644)]
    for rel in arquivos:
        modo = 0o755 if Path(rel).suffix.lower() in {".sh", ".command"} else 0o644
        entradas.append((f"{prefixo}/{rel}", (ROOT / rel).read_bytes(), modo))

    with sdist.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(mode="w", fileobj=gz, format=tarfile.PAX_FORMAT) as tf:
                for arcname, dados, modo in sorted(entradas, key=lambda item: item[0]):
                    info = tarfile.TarInfo(name=arcname)
                    info.size = len(dados)
                    info.mtime = EPOCA_FIXA
                    info.mode = modo
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    tf.addfile(info, io.BytesIO(dados))
    return sdist


def membros_proibidos(nome: str, dados: bytes, caminho: str = "") -> list[str]:
    """Motivos pelos quais um membro (e, recursivamente, um zip/wheel/tar.gz aninhado) contém PyMuPDF/MuPDF."""
    achados = []
    if NOME_MUPDF.search(nome):
        achados.append(f"{caminho}{nome}: nome")
    if zipfile.is_zipfile(io.BytesIO(dados)):
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            for info in z.infolist():
                achados += membros_proibidos(info.filename, z.read(info), f"{caminho}{nome}!")
    elif nome.lower().endswith((".tar.gz", ".tgz", ".tar")) and tarfile.is_tarfile(io.BytesIO(dados)):
        with tarfile.open(fileobj=io.BytesIO(dados), mode="r:*") as tf:
            for info in tf.getmembers():
                if info.isfile():
                    extraido = tf.extractfile(info)
                    if extraido is not None:
                        achados += membros_proibidos(info.name, extraido.read(), f"{caminho}{nome}!")
    elif Path(nome).suffix.lower() in TEXTUAIS and not utf8_valido(dados):
        achados.append(f"{caminho}{nome}: não UTF-8 (membro textual do pacote deve ser UTF-8, com ou sem BOM)")
    elif e_binario(dados) and CONTEUDO_MUPDF.search(dados):  # texto UTF-8 válido pode citar MuPDF (ex.: THIRD-PARTY.md)
        achados.append(f"{caminho}{nome}: conteúdo")
    return achados


LICENCE_SHA256 = "8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef"
SPDX_TAG = "SPDX-License-Identifier: AGPL-3.0-or-later"


def conferir_licenca_e_spdx(p: Path) -> list[str]:
    erros: list[str] = []
    nome = p.name
    low = nome.lower()
    if Path(low).suffix in (".py", ".ps1", ".sh"):
        texto = p.read_bytes().decode("utf-8-sig", errors="replace")
        if SPDX_TAG not in texto:
            erros.append(f"{nome}: cabeçalho SPDX ausente")
        return erros
    if not low.startswith("traslado-"):
        return erros
    if low.endswith((".zip", ".whl")):
        with zipfile.ZipFile(p) as z:
            lics = [n for n in z.namelist() if n == "LICENCE" or n.endswith("/LICENCE")]
            if not lics:
                erros.append(f"{nome}: LICENCE ausente")
            for n in lics:
                if sha(z.read(n)) != LICENCE_SHA256:
                    erros.append(f"{nome}!{n}: SHA-256 de LICENCE diverge de {LICENCE_SHA256}")
            for n in z.namelist():
                partes = n.split("/")
                if any(parte in ("tests", ".github", "release-files.txt") for parte in partes):
                    erros.append(f"{nome}!{n}: caminho proibido no artefato")
                if Path(n).suffix.lower() in (".py", ".ps1", ".sh"):
                    texto = z.read(n).decode("utf-8-sig", errors="replace")
                    if SPDX_TAG not in texto:
                        erros.append(f"{nome}!{n}: cabeçalho SPDX ausente")
    elif low.endswith(".tar.gz"):
        with tarfile.open(p, "r:gz") as tf:
            membros = [m for m in tf.getmembers() if m.isfile()]
            lics = [m for m in membros if m.name == "LICENCE" or m.name.endswith("/LICENCE")]
            if not lics:
                erros.append(f"{nome}: LICENCE ausente")
            for m in lics:
                ext = tf.extractfile(m)
                if ext is None or sha(ext.read()) != LICENCE_SHA256:
                    erros.append(f"{nome}!{m.name}: SHA-256 de LICENCE diverge de {LICENCE_SHA256}")
            for m in membros:
                partes = m.name.split("/")
                if ".github" in partes:
                    erros.append(f"{nome}!{m.name}: caminho proibido no artefato")
                if Path(m.name).suffix.lower() in (".py", ".ps1", ".sh"):
                    ext = tf.extractfile(m)
                    texto = ext.read().decode("utf-8-sig", errors="replace") if ext is not None else ""
                    if SPDX_TAG not in texto:
                        erros.append(f"{nome}!{m.name}: cabeçalho SPDX ausente")
    return erros


def conferir_composicao(p: Path) -> list[str]:
    """Compare membros contra a seleção pública, inclusive dist-info gerado."""
    nomes = {n.strip() for n in (ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines()
             if n.strip() and not n.startswith(("#", "!"))}
    prefix = f"traslado-{VERSION}"
    if p.name == prefix + "-py3-none-any.whl":
        info = prefix + ".dist-info/"
        expected = {n.removeprefix("src/") for n in nomes if n.startswith("src/")}
        expected |= {info + n for n in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD", "licenses/LICENCE")}
        with zipfile.ZipFile(p) as z:
            actual = z.namelist()
    elif p.name == prefix + "-windows.zip":
        expected = {prefix + "-windows/" + n for n in nomes | {"INSTALL-PAYLOAD.json", "PAYLOAD-SHA256SUMS", f"dist/{prefix}-py3-none-any.whl"}}
        with zipfile.ZipFile(p) as z:
            actual = z.namelist()
    elif p.name == prefix + ".tar.gz":
        tracked = set(git("ls-files", "-z").split("\0"))
        expected = {prefix + "/" + n for n in nomes | {"PKG-INFO", "release-files.txt"} |
                    {n for n in tracked if n.startswith("tests/") and (ROOT/n).is_file()}}
        with tarfile.open(p) as tf:
            actual = [m.name for m in tf.getmembers() if m.isfile()]
    else:
        return []
    errors = []
    if len(actual) != len(set(actual)):
        errors.append(f"{p.name}: membros duplicados")
    if set(actual) != expected:
        errors.append(f"{p.name}: composição diverge: a mais {sorted(set(actual)-expected)}; a menos {sorted(expected-set(actual))}")
    return errors


def verificar(arquivos: list[Path]) -> int:
    achados = [a for p in arquivos for a in [*membros_proibidos(p.name, p.read_bytes()), *conferir_licenca_e_spdx(p), *conferir_composicao(p)]]
    for a in achados:
        print("Falha de verificação de artefato:", a, file=sys.stderr)
    return 1 if achados else 0


def _emitir_artefatos_bootstrap_e_locks(dist: Path) -> list[Path]:
    """Emite install.ps1 e install.sh (com versão embutida) e os locks universais em dist/."""
    ps1_src = (ROOT / "install.ps1").read_text(encoding="utf-8")
    ps1_out = re.sub(r'(\[string\]\$Version\s*=\s*)"[^"]+"', rf'\1"{VERSION}"', ps1_src, count=1)
    ps1_dest = dist / "install.ps1"
    ps1_dest.write_text(ps1_out, encoding="utf-8", newline="\n")

    sh_src = (ROOT / "install.sh").read_text(encoding="utf-8")
    sh_out = re.sub(r'(^VERSION=)"[^"]+"', rf'\1"{VERSION}"', sh_src, count=1, flags=re.M)
    sh_dest = dist / "install.sh"
    sh_dest.write_text(sh_out, encoding="utf-8", newline="\n")

    gerados = [ps1_dest, sh_dest]
    for perfil in ("core", "full", "legacy"):
        nome_lock = f"requirements-{perfil}.hashes.lock"
        origem = ROOT / "locks" / nome_lock
        destino = dist / nome_lock
        shutil.copyfile(origem, destino)
        gerados.append(destino)
    return gerados


def construir(dist: Path, permitir_sujo: bool = False) -> tuple[Path, Path]:
    nomes = listados(permitir_nao_rastreados=permitir_sujo)
    limpo = checkout_limpo(nomes)
    if not limpo and not permitir_sujo:
        raise SystemExit("Checkout sujo ou arquivo listado difere de HEAD: recusado (--permitir-sujo só para teste local).")
    dist.mkdir(parents=True, exist_ok=True)
    for velho in [*dist.glob("*.whl"), *dist.glob("*.tar.gz")]:
        velho.unlink()
    wheel = construir_wheel(dist, nomes)
    sdist = construir_sdist(dist, nomes, wheel)
    extras_dist = _emitir_artefatos_bootstrap_e_locks(dist)
    itens = {n: (ROOT / n).read_bytes() for n in nomes}
    itens[f"dist/{wheel.name}"] = wheel.read_bytes()  # o instalador exige o wheel próprio no payload
    payload = {"schema_version": 1, "product": PRODUCT, "version": VERSION, "checkout_limpo": limpo,
               "commit": git("rev-parse", "HEAD").strip(),
               "files": [{"path": n, "sha256": sha(b), "bytes": len(b)} for n, b in sorted(itens.items())]}
    itens["INSTALL-PAYLOAD.json"] = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    # Cópia na raiz (ignorada pelo git): testes e instalador a usam quando a raiz atua como pacote.
    (ROOT / "INSTALL-PAYLOAD.json").write_bytes(itens["INSTALL-PAYLOAD.json"])
    # Flat asset names match both bootstrap lookup and GitHub release assets.
    bootstrap_members = ["install.sh", "install.ps1", "INSTALAR.bat", "instalar.sh", "instalar.command",
                         "ocr-assets.lock.json", "scripts/bootstrap_ocr.ps1", f"dist/{wheel.name}",
                         *(f"locks/requirements-{profile}.hashes.lock" for profile in ("core", "full", "legacy"))]
    payload_sums = {Path(n).name: sha(itens[n]) for n in bootstrap_members}
    itens["PAYLOAD-SHA256SUMS"] = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(payload_sums.items())
    ).encode("utf-8")
    internal_sums = dist / "PAYLOAD-SHA256SUMS"
    internal_sums.write_bytes(itens["PAYLOAD-SHA256SUMS"])
    nome = f"traslado-{VERSION}-windows"
    zipado = dist / f"{nome}.zip"
    with zipfile.ZipFile(zipado, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho, dados in sorted(itens.items()):
            info = zipfile.ZipInfo(f"{nome}/{caminho}", DATA_FIXA)
            info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 3, 0o100644 << 16
            z.writestr(info, dados)
    inventario_dist = dist / "THIRD-PARTY-INVENTORY.json"
    shutil.copyfile(ROOT / "THIRD-PARTY-INVENTORY.json", inventario_dist)
    todos_artefatos = [zipado, wheel, sdist, internal_sums, *extras_dist, inventario_dist]
    if verificar(todos_artefatos):
        for arq in todos_artefatos:
            arq.unlink(missing_ok=True)
        raise SystemExit("Composição ou licença reprovada: artefatos removidos.")
    (dist / "SHA256SUMS").write_text(
        "".join(f"{sha(p)}  {p.name}\n" for p in todos_artefatos),
        encoding="utf-8",
        newline="\n",
    )
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
