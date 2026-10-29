# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Mutação determinística dos formatos nativos (E6): semente fixa e 200 mutações por fixture."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
import importlib.util
import io
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("T11_EXIGE_INSTALADO") and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from traslado.model import Options
from traslado.runner import execute
from traslado.errors import CODES

TYPED_CODES = frozenset(CODES - {"internal"})

import traslado
ASSETS = Path(traslado.__file__).resolve().parent / "assets"
LEGACY_FIXTURES = ROOT / "tests/fixtures/legacy"
SEED = 20260420


def _mutate(payload: bytes, rng: random.Random, index: int) -> bytes:
    if not payload:
        return b"\x00"
    strategy = index % 5
    if strategy == 0:
        cut = rng.randrange(0, max(1, len(payload)))
        return payload[:cut]
    if strategy == 1:
        buf = bytearray(payload)
        flips = rng.randrange(1, min(32, len(buf) + 1))
        for _ in range(flips):
            pos = rng.randrange(len(buf))
            buf[pos] ^= rng.randrange(1, 256)
        return bytes(buf)
    if strategy == 2:
        buf = bytearray(payload)
        start = rng.randrange(len(buf))
        width = rng.randrange(1, min(64, len(buf) - start + 1))
        buf[start : start + width] = b"\x00" * width
        return bytes(buf)
    if strategy == 3:
        pos = rng.randrange(len(payload) + 1)
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
        return payload[:pos] + junk + payload[pos:]
    buf = bytearray(payload)
    head = min(len(buf), rng.randrange(1, 24))
    buf[:head] = bytes(rng.randrange(256) for _ in range(head))
    return bytes(buf)


def _build_minimal_xlsx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Planilha1" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Coluna</t></is></c>'
            '<c r="B1" t="n"><v>42</v></c></row></sheetData>'
            "</worksheet>",
        )
    return buf.getvalue()


def _native_fixtures() -> dict[str, bytes]:
    fixtures: dict[str, bytes] = {
        "txt": "# Título Nativo\n\nParágrafo com acentuação e números 12345.\n".encode("utf-8"),
        "csv": "id,nome,valor\n1,Item A,10.5\n2,Item B,20.0\n".encode("utf-8"),
        "json": '{"chave": "valor", "lista": [1, 2, 3], "ativo": true}\n'.encode("utf-8"),
        "xml": '<?xml version="1.0" encoding="UTF-8"?><raiz><item id="1">Texto XML</item></raiz>\n'.encode("utf-8"),
        "html": "<!DOCTYPE html><html><head><title>Teste</title></head><body><h1>Título</h1><p>Corpo HTML.</p></body></html>\n".encode("utf-8"),
    }
    if importlib.util.find_spec("docx") is not None and (ASSETS / "fixture.docx").is_file():
        fixtures["docx"] = (ASSETS / "fixture.docx").read_bytes()
    if importlib.util.find_spec("openpyxl") is not None:
        fixtures["xlsx"] = _build_minimal_xlsx()
    if importlib.util.find_spec("pptx") is not None and (LEGACY_FIXTURES / "ppt-fonte.pptx").is_file():
        fixtures["pptx"] = (LEGACY_FIXTURES / "ppt-fonte.pptx").read_bytes()
    if importlib.util.find_spec("fitz") is not None and (ASSETS / "fixture.pdf").is_file():
        fixtures["pdf"] = (ASSETS / "fixture.pdf").read_bytes()
    if importlib.util.find_spec("PIL") is not None and (ASSETS / "traslado.png").is_file():
        fixtures["png"] = (ASSETS / "traslado.png").read_bytes()
    if os.environ.get("TRASLADO_REQUIRE_NATIVE_FIXTURES"):
        assert set(fixtures) == {"txt", "csv", "json", "xml", "html", "docx", "xlsx", "pptx", "pdf", "png"}, set(fixtures)
    return fixtures


class NativeMutationE6Tests(unittest.TestCase):
    def test_200_mutacoes_deterministicas_por_fixture_nativa(self) -> None:
        count = 200
        fixtures = _native_fixtures()
        self.assertGreaterEqual(len(fixtures), 5)
        opts = Options(
            mode="native",
            images=False,
            tables=False,
            chunks=False,
            job_timeout=15,
            max_memory_mb=512,
            max_output_bytes=2_000_000,
            max_archive_bytes=4_000_000,
            ocr_timeout=5,
        )
        rng = random.Random(SEED)
        failures: list[tuple[str, int, dict]] = []

        with tempfile.TemporaryDirectory(prefix="e6-native-mut-") as tmp:
            base = Path(tmp)
            cases: list[tuple[str, int, bytes]] = []
            for fmt, original in sorted(fixtures.items()):
                for idx in range(count):
                    cases.append((fmt, idx, _mutate(original, rng, idx)))

            def _run_case(case: tuple[str, int, bytes]) -> None:
                fmt, idx, mutated = case
                folder = base / f"{fmt}_{idx:03d}"
                folder.mkdir(parents=True, exist_ok=True)
                src = folder / f"mut_{fmt}_{idx:03d}.{fmt}"
                src.write_bytes(mutated)
                dest = folder / "out"
                try:
                    res = execute(src, dest, opts)
                    status = res.get("status")
                    code = res.get("error_code")
                    if status not in {"complete", "partial", "failed", "timeout"}:
                        failures.append((fmt, idx, res))
                        retained = ROOT / "work/native-mutant-failures"
                        retained.mkdir(parents=True, exist_ok=True)
                        (retained / f"{fmt}-{idx}.{fmt}").write_bytes(mutated)
                    elif code == "internal" or (status in {"failed", "timeout"} and code not in TYPED_CODES) or \
                            (status == "timeout" and code != "timeout") or (status in {"complete", "partial"} and code not in (None, *TYPED_CODES)):
                        failures.append((fmt, idx, res))
                        retained = ROOT / "work/native-mutant-failures"
                        retained.mkdir(parents=True, exist_ok=True)
                        (retained / f"{fmt}-{idx}.{fmt}").write_bytes(mutated)
                    return fmt, status, code
                finally:
                    shutil.rmtree(folder, ignore_errors=True)

            with ThreadPoolExecutor(max_workers=4) as pool:
                counts = Counter(pool.map(_run_case, cases))
            evidence = ROOT / "work/native-mutation-results.json"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps({str(k): v for k, v in sorted(counts.items(), key=str)}, indent=2), encoding="utf-8")

        self.assertEqual(failures, [], f"Falhas inesperadas na mutação nativa: {failures[:5]}")
        # Entradas determinísticas, limites folgados (15 s): a linha de base da CI não tem nenhum timeout.
        # Tolera-se um por carga do executor; mais que isso indica travamento convertido em timeout.
        timeouts = sum(n for (_fmt, status, _code), n in counts.items() if status == "timeout")
        self.assertLessEqual(timeouts, 1, f"Timeouts na mutação nativa: {timeouts} ({dict(counts)})")


if __name__ == "__main__":
    unittest.main()
