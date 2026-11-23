# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Oráculo dos contratos v2 (documento e chunks): exemplos válidos passam; cada inválido falha com o código declarado;
os exemplos versionados são exatamente os que o gerador produz. Exige jsonschema (casos de código "schema")."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
EX = RAIZ / "docs" / "contratos" / "exemplos"
VALIDADOR = RAIZ / "scripts" / "validar_modelo_v2.py"
VALIDADOR_CK = RAIZ / "scripts" / "validar_chunks_v2.py"


def rodar(arq: Path) -> tuple[int, set[str]]:
    r = subprocess.run([sys.executable, "-X", "utf8", str(VALIDADOR), str(arq)], capture_output=True, text=True, encoding="utf-8")
    return r.returncode, {linha.split(": ")[1] for linha in r.stdout.splitlines() if linha.count(": ") >= 2}


def rodar_chunks(ck: Path, md: Path, doc: Path) -> tuple[int, set[str]]:
    r = subprocess.run([sys.executable, "-X", "utf8", str(VALIDADOR_CK), str(ck), str(md), str(doc)],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, {linha.split(": ")[1] for linha in r.stdout.splitlines() if linha.count(": ") >= 2}


def main() -> int:
    import jsonschema  # noqa: F401  (falha cedo: sem ele os casos estruturais passariam por omissão)
    falhas = []
    for arq in sorted((EX / "validos").glob("*.json")):
        rc, codigos = rodar(arq)
        if rc != 0:
            falhas.append(f"{arq.name}: válido recusado {sorted(codigos)}")
    esperado = json.loads((EX / "esperado.json").read_text(encoding="utf-8"))
    presentes = {a.name for a in (EX / "invalidos").glob("*.json")}
    if presentes != set(esperado):
        falhas.append(f"inválidos sem expectativa ou ausentes: {sorted(presentes ^ set(esperado))}")
    for nome, codigo in sorted(esperado.items()):
        rc, codigos = rodar(EX / "invalidos" / nome)
        if rc != 1 or codigo not in codigos:
            falhas.append(f"{nome}: esperado {codigo}, obtido rc={rc} {sorted(codigos)}")
    ck = EX / "chunks"
    minimo = EX / "validos" / "minimo.json"
    rc, codigos = rodar_chunks(ck / "valido.jsonl", ck / "document.md", minimo)
    if rc != 0:
        falhas.append(f"chunks/valido.jsonl: válido recusado {sorted(codigos)}")
    esperado_ck = json.loads((ck / "esperado.json").read_text(encoding="utf-8"))
    presentes_ck = {a.name for a in (ck / "invalidos").glob("*.jsonl")}
    if presentes_ck != set(esperado_ck):
        falhas.append(f"chunks inválidos sem expectativa ou ausentes: {sorted(presentes_ck ^ set(esperado_ck))}")
    for nome, codigo in sorted(esperado_ck.items()):
        rc, codigos = rodar_chunks(ck / "invalidos" / nome, ck / "document.md", minimo)
        if rc != 1 or codigo not in codigos:
            falhas.append(f"chunks/{nome}: esperado {codigo}, obtido rc={rc} {sorted(codigos)}")
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "repo"
        (copia / "scripts").mkdir(parents=True)
        (copia / "scripts" / "gerar_exemplos_v2.py").write_bytes((RAIZ / "scripts" / "gerar_exemplos_v2.py").read_bytes())
        subprocess.run([sys.executable, "-X", "utf8", str(copia / "scripts" / "gerar_exemplos_v2.py")], check=True, capture_output=True)
        for gerado in (copia / "docs" / "contratos" / "exemplos").rglob("*"):
            if gerado.is_dir():
                continue
            versionado = EX / gerado.relative_to(copia / "docs" / "contratos" / "exemplos")
            if not versionado.exists() or versionado.read_bytes() != gerado.read_bytes():
                falhas.append(f"exemplo divergente do gerador: {versionado.relative_to(RAIZ)}")
    for f in falhas:
        print(f)
    print(f"{len(esperado)} inválidos, {len(falhas)} falha(s)")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
