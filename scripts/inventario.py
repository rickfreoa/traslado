# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Confronta THIRD-PARTY-INVENTORY.json com o que o Traslado distribui ou baixa (A8).

Uso: python scripts/inventario.py [--raiz DIR] [--saida ARQUIVO]
Confere: dependências resolvidas (requirements-full.lock e .hashes.lock), downloads de ocr-assets.lock.json,
membros extraídos pelo bootstrap (Tesseract e 7-Zip), DLLs nativas, modelos tessdata e licenças.
Imprime e grava as divergências (JSON); código 1 se houver item distribuído ou baixado sem entrada de
inventário ou sem licença. Lacunas conhecidas e aceitas ficam em "lacunas" no próprio inventário e são
sempre listadas; nunca silenciadas.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def norm(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def lock_python(texto: str) -> dict[str, str]:
    """name==version -> {nome_normalizado: versão}; ignora comentários, continuações e opções."""
    pares = {}
    for linha in texto.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", linha)
        if m:
            pares[norm(m[1])] = m[2]
    return pares


def hashes_por_pacote(texto: str) -> dict[str, int]:
    contagem, atual = {}, None
    for linha in texto.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", linha)
        if m:
            atual = norm(m[1])
            contagem[atual] = 0
        if atual:
            contagem[atual] += len(re.findall(r"--hash=sha256:[0-9a-f]{64}", linha))
    return contagem


def verificar(raiz: Path) -> tuple[list[str], list[str]]:
    div: list[str] = []
    ler = lambda n: (raiz / n).read_text(encoding="utf-8-sig")  # noqa: E731
    inv = json.loads(ler("THIRD-PARTY-INVENTORY.json"))
    lock = json.loads(ler("ocr-assets.lock.json"))

    # 1. Dependências Python resolvidas
    fixas = lock_python(ler("requirements-full.lock"))
    com_hash = hashes_por_pacote(ler("requirements-full.hashes.lock"))
    inventariadas = {norm(d["name"]): d for d in inv["dependencies"]}
    for nome, versao in sorted(fixas.items()):
        d = inventariadas.get(nome)
        if d is None:
            div.append(f"dependência sem entrada no inventário: {nome}=={versao}")
            continue
        if d["version"] != versao:
            div.append(f"versão divergente no inventário: {nome} lock={versao} inventário={d['version']}")
        if not (d.get("license_expression") or d.get("license_metadata")):
            div.append(f"dependência sem licença no inventário: {nome}")
    # O lock com hashes é o efetivamente instalado (INSTALAR.ps1 e CI): confronta nome->versão com o outro lock e com o inventário.
    fixas_hash = lock_python(ler("requirements-full.hashes.lock"))
    for nome in sorted(fixas.keys() | fixas_hash.keys()):
        if fixas.get(nome) != fixas_hash.get(nome):
            div.append(f"lock com hashes diverge de requirements-full.lock: {nome} lock={fixas.get(nome)} hashes={fixas_hash.get(nome)}")
        d = inventariadas.get(nome)
        if nome in fixas_hash and (d is None or d["version"] != fixas_hash[nome]):
            div.append(f"inventário diverge do lock com hashes: {nome} hashes={fixas_hash[nome]} "
                       f"inventário={d['version'] if d else 'ausente'}")
    div += [f"dependência sem hash SHA-256 no lock com hashes: {n}" for n in fixas if not com_hash.get(n)]
    testes = lock_python(ler("requirements-test.hashes.lock"))
    div += [f"dependência de teste sem menção em THIRD-PARTY.md: {n}" for n in testes if n not in ler("THIRD-PARTY.md").lower()]

    # 2. Downloads fixados
    baixados = {d["name"]: d for d in inv["ocr_downloads"]}
    bootstrap = ler("scripts/bootstrap_ocr.ps1")
    for a in lock["assets"]:
        d = baixados.get(a["name"])
        if d is None:
            div.append(f"download sem entrada no inventário: {a['name']}")
            continue
        for campo in ("sha256", "bytes", "url"):
            if d.get(campo) != a[campo]:
                div.append(f"download {a['name']}: {campo} diverge do lock")
        if not a["name"].endswith(".traineddata") and a["name"] not in bootstrap:
            div.append(f"asset do lock não referenciado pelo bootstrap: {a['name']}")
    div += [f"inventário lista download fora do lock: {n}" for n in baixados if n not in {a["name"] for a in lock["assets"]}]
    if inv.get("ocr_assets_lock_sha256") != hashlib.sha256((raiz / "ocr-assets.lock.json").read_bytes()).hexdigest():
        div.append("ocr_assets_lock_sha256 do inventário não corresponde ao ocr-assets.lock.json")

    # 3. Membros extraídos (Tesseract e 7-Zip) e DLLs nativas
    def comparar(titulo: str, do_lock: list[dict], do_inv: list[dict]) -> None:
        a, b = {m["path"]: m for m in do_lock}, {m["path"]: m for m in do_inv}
        div.extend(f"{titulo}: membro sem entrada no inventário: {p}" for p in sorted(a.keys() - b.keys()))
        div.extend(f"{titulo}: inventário lista membro fora do lock: {p}" for p in sorted(b.keys() - a.keys()))
        div.extend(f"{titulo}: hash/tamanho diverge: {p}" for p in sorted(a.keys() & b.keys())
                   if (a[p]["sha256"], a[p]["bytes"]) != (b[p]["sha256"], b[p]["bytes"]))

    comparar("tesseract_members", lock["tesseract_members"], inv["ocr_engine"]["runtime_members"])
    comparar("seven_zip_members", lock["seven_zip_members"], inv["bootstrap_extractor"]["members"])
    lacunas = list(inv.get("lacunas", []))
    declaradas = {x.get("id") for x in lacunas if isinstance(x, dict)}
    dlls = {d["path"]: d for d in inv["ocr_native_dlls"]}
    for m in lock["tesseract_members"]:
        if m["path"].lower().endswith(".dll"):
            d = dlls.get(m["path"])
            if d is None or d["sha256"] != m["sha256"]:
                div.append(f"DLL nativa sem entrada válida no inventário: {m['path']}")
            elif not d.get("license_expression") and "dlls-nativas-licenca-nao-auditada" not in declaradas:
                div.append(f"DLL nativa sem licença e sem lacuna declarada: {m['path']}")

    # 4. Licenças do motor, do extrator e dos modelos
    if not inv["ocr_engine"].get("license_expression"):
        div.append("motor Tesseract sem licença no inventário")
    if not inv["bootstrap_extractor"].get("licenses"):
        div.append("extrator 7-Zip sem licença no inventário")
    modelos = inv["ocr_model_license"]
    if not modelos.get("expression") or modelos.get("repository_commit") != lock["tessdata_fast_commit"]:
        div.append("modelos tessdata sem licença ou commit diverge do lock")
    esperados = sorted(a["name"].removesuffix(".traineddata") for a in lock["assets"] if a["name"].endswith(".traineddata"))
    if sorted(modelos.get("languages", [])) != esperados or sorted(lock["default_languages"]) != esperados:
        div.append("idiomas tessdata divergem entre lock e inventário")
    if "TESSDATA-LICENSE.txt" not in baixados:
        div.append("licença dos modelos tessdata não está entre os downloads")

    if lock["platform"] == "windows-x64" and not (
            "plataforma-ocr-so-windows-x64" in declaradas and re.search(r"linux", ler("THIRD-PARTY.md"), re.I)):
        div.append("lock OCR só cobre windows-x64: lacuna Linux não declarada no inventário e em THIRD-PARTY.md")
    return div, [f"{x['id']}: {x['descricao']}" if isinstance(x, dict) else str(x) for x in lacunas]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", type=Path, default=RAIZ)
    ap.add_argument("--saida", type=Path)
    args = ap.parse_args()
    div, lacunas = verificar(args.raiz)
    saida = args.saida or args.raiz / "dist" / "inventario-divergencias.json"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps({"divergencias": div, "lacunas_declaradas": lacunas}, indent=2, ensure_ascii=False) + "\n",
                     encoding="utf-8")
    for d in div:
        print("DIVERGENCIA:", d, file=sys.stderr)
    for lacuna in lacunas:
        print("LACUNA DECLARADA:", lacuna)
    print(f"Inventário: {len(div)} divergência(s), {len(lacunas)} lacuna(s) declarada(s).")
    sys.exit(1 if div else 0)


if __name__ == "__main__":
    main()
