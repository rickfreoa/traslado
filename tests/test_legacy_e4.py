"""Independent structural oracles for CC0 fixtures; native parser stays contained."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import shutil
import socket
import tempfile
import uuid
import unittest
from unittest.mock import patch

from traslado.errors import ConversionError
from traslado.model import Document, Options
from traslado.runner import execute

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
FORMATS = ("rtf", "odt", "ods", "odp", "epub", "doc", "ppt")
HAS_LEGACY = importlib.util.find_spec("anydoc") is not None


def fixture(fmt):
    path = FIXTURES / ("publico." + fmt)
    if not path.is_file():
        raise unittest.SkipTest("NOT_RUN " + fmt + ": fixture COM indisponível; gerar.ps1 requer Word/PowerPoint (0x80080005 nesta máquina)")
    return path


@contextmanager
def scratch():
    # Inherit workspace ACL; Python 3.14 mode 0700 is inaccessible to this sandbox.
    root = Path(__file__).resolve().parents[1] / "work" / ("e4-" + uuid.uuid4().hex)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


class LegacyOptIn(unittest.TestCase):
    @unittest.skipUnless(HAS_LEGACY, "extra legacy ausente: pip install traslado[legacy]")
    def test_unsupported_zip_method_and_encryption_are_typed(self):
        import io
        import zipfile
        from traslado.detect import sniff
        from traslado.legacy import extract
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        with scratch() as root:
            source = root / "bad.odt"
            for encrypted, expected in ((False, "unsupported"), (True, "encrypted")):
                payload = bytearray(buffer.getvalue())
                central = payload.index(b"PK\x01\x02")
                if encrypted:
                    payload[6:8] = (1).to_bytes(2, "little")
                    payload[central+8:central+10] = (1).to_bytes(2, "little")
                else:
                    payload[8:10] = (99).to_bytes(2, "little")
                    payload[central+10:central+12] = (99).to_bytes(2, "little")
                source.write_bytes(payload)
                with self.subTest(encrypted=encrypted):
                    self.assertIsNone(sniff(source))
                    with self.assertRaises(ConversionError) as caught:
                        extract(source, root, Options(legacy=True), Document(source.name, "hash"))
                    self.assertEqual(caught.exception.code, expected)
                    result = execute(source, root / "out", Options(legacy=True))
                    self.assertEqual(result.get("error_code"), expected, result)

    def test_ole_content_detection_is_bounded_and_opt_in(self):
        from traslado.detect import sniff
        with scratch() as root:
            source = root / "renamed.bin"
            for stream_name, suffix in (("WordDocument", ".doc"), ("PowerPoint Document", ".ppt"), ("Workbook", ".xls")):
                header = bytearray(512)
                header[:8] = bytes.fromhex("D0CF11E0A1B11AE1")
                header[30:32] = (9).to_bytes(2, "little")
                header[48:52] = (0).to_bytes(4, "little")
                entry = bytearray(512)
                name = (stream_name + "\0").encode("utf-16-le")
                entry[:len(name)] = name
                entry[64:66] = len(name).to_bytes(2, "little")
                source.write_bytes(header + entry)
                self.assertEqual(sniff(source), "ole2")
                self.assertEqual(sniff(source, legacy=True), suffix)

    def test_disabled_and_missing_extra(self):
        from traslado.legacy import extract
        source = FIXTURES / "publico.rtf"
        with scratch() as root:
            result = execute(source, root)
            self.assertEqual(result["error_code"], "unsupported", result)
            self.assertIn("--engine legacy", result["error"])
            if not HAS_LEGACY:
                result = execute(source, root, Options(legacy=True))
                self.assertEqual(result["error_code"], "missing_part", result)
                self.assertIn("pip install traslado[legacy]", result["error"])
        with self.assertRaises(ValueError):
            Options(legacy="yes")

    def test_optional_fields_absent_in_existing_document(self):
        data = Document("old.txt", "hash", options=asdict(Options())).to_dict()
        self.assertFalse({"submotor", "submotor_version", "experimental"} & data.keys())
        self.assertNotIn("legacy", data["options"])


@unittest.skipUnless(HAS_LEGACY, "extra legacy ausente: pip install traslado[legacy]")
class LegacyE4(unittest.TestCase):
    def test_structural_gates(self):
        with scratch() as root:
            for fmt in FORMATS:
                with self.subTest(format=fmt):
                    source = fixture(fmt)
                    expected = json.loads(source.with_suffix(source.suffix + ".expected.json").read_text("utf-8"))
                    result = execute(source, root, Options(legacy=True, chunks=True))
                    self.assertEqual(result["status"], "partial", result)
                    folder = Path(result["output"])
                    data = json.loads((folder / "document.json").read_text("utf-8"))
                    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
                    md = (folder / "document.md").read_bytes().decode("utf-8")
                    for payload in (data, manifest):
                        self.assertEqual(payload["submotor"], "anydoc")
                        self.assertEqual(payload["submotor_version"], "0.2.4")
                        self.assertIs(payload["experimental"], True)
                        self.assertEqual(payload["fidelity"], "unverified")
                        self.assertEqual(payload["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                    self.assertEqual([p["number"] for p in data["pages"]], [1])
                    self.assertEqual(data["pages"][0]["route"], "legacy")
                    self.assertTrue({"legacy_engine_experimental", "legacy_layout_unavailable"} <= set(data["warnings"]))
                    blocks = data["pages"][0]["blocks"]
                    self.assertEqual(dict(Counter(b["kind"] for b in blocks)), expected["blocks"])
                    self.assertEqual(dict(Counter(b["details"].get("legacy_kind", "note_definition") for b in blocks)), expected["source_blocks"])
                    calls = re.findall(r"\[\^([^\]]+)\](?!:)", md)
                    self.assertEqual(calls, [n["label"] for n in expected["notes"]])
                    definitions = re.findall(r"(?m)^\[\^([^\]]+)\]: (.*)$", md)
                    self.assertEqual(definitions, [(n["label"], n["text"]) for n in expected["notes"]])
                    for note in expected["notes"]:
                        self.assertIn(note["before"] + "[^" + note["label"] + "]" + note["after"], md)
                        matching = [b for b in blocks if b["text"].startswith("[^" + note["label"] + "]: ")]
                        self.assertEqual(len(matching), 1)
                        self.assertEqual(matching[0]["details"]["note_family"], note["family"])
                    cursor = 0
                    for text in expected["ordered"]:
                        cursor = md.index(text, cursor) + len(text)
                    for chunk in map(json.loads, (folder / "chunks/chunks.jsonl").read_text("utf-8").splitlines()):
                        self.assertEqual(md[chunk["char_start"]:chunk["char_end"]], chunk["text"])
                        self.assertIn("legacy_engine_experimental", chunk["warnings"])
                    if expected.get("merged"):
                        self.assertIn("legacy_table_merged_cells_need_review", data["pages"][0]["warnings"])
                        table = next(b for b in blocks if b["kind"] == "table")
                        self.assertEqual(table["rows"][0], ["Norte unido", ""])

    def test_content_dispatch_all_formats_and_no_automatic_replacement(self):
        from traslado.detect import sniff
        with scratch() as root:
            for fmt in FORMATS:
                with self.subTest(format=fmt):
                    source = root / (fmt + ".bin")
                    source.write_bytes(fixture(fmt).read_bytes())
                    self.assertEqual(sniff(source, legacy=True), "." + fmt)
                    first = execute(source, root / "out", Options(legacy=True))
                    second = execute(source, root / "out", Options(legacy=True))
                    self.assertEqual(first["status"], "partial", first)
                    self.assertEqual(second["status"], "partial", second)
                    self.assertNotEqual(first["output"], second["output"])
            renamed = root / "renamed.odt"
            renamed.write_bytes(fixture("rtf").read_bytes())
            result = execute(renamed, root / "out", Options(legacy=True))
            self.assertEqual(result["status"], "partial", result)
            self.assertEqual(result["warning_summary"]["format_extension_mismatch"], 1)

    def test_no_network_including_import(self):
        # A fresh interpreter tests package import as well as native conversion.
        import subprocess
        import sys
        code = '''
import socket
def forbidden(*a, **k): raise AssertionError("network attempted")
socket.socket = socket.create_connection = forbidden
from pathlib import Path
import importlib.util
import sys
if importlib.util.find_spec('traslado') is None:
    sys.path.insert(0, sys.argv[3])
from traslado.legacy import extract
from traslado.model import Document, Options
assert 'anydoc' not in __import__('sys').modules
root = Path(__import__('sys').argv[1])
for source in Path(__import__('sys').argv[2]).glob('publico.*'):
    if source.suffix in {'.rtf','.odt','.ods','.odp','.epub','.doc','.ppt'}:
        extract(source, root, Options(legacy=True), Document(source.name, 'a'*64))
assert 'anydoc' not in __import__('sys').modules
'''
        with scratch() as root:
            src = Path(__file__).resolve().parents[1] / "src"
            proc = subprocess.run([sys.executable, "-I", "-c", code, str(root), str(FIXTURES.resolve()), str(src)],
                                  capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_config_and_doctor(self):
        import subprocess
        import sys
        with scratch() as root:
            config = root / "legacy.toml"
            config.write_text("[conversion]\nlegacy = true\n", "utf-8")
            for flags in (["--engine", "legacy"], ["--config", str(config)]):
                proc = subprocess.run([sys.executable, "-P", "-m", "traslado", "convert",
                                      str(FIXTURES / "publico.rtf"), "-o", str(root / "out"), *flags],
                                      capture_output=True, timeout=120)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)["status"], "partial")
            from traslado.doctor import inspect
            with patch("traslado.doctor.find_tesseract", return_value=None), patch("traslado.registry.ocr.find_tesseract", return_value=None):
                report = inspect()
            self.assertTrue(report["capabilities"]["anydoc"]["available"])
            self.assertEqual(next(c for c in report["converters"] if c["id"] == "legacy")["state"], "available")

    def test_remaining_model_kinds_images_and_limits(self):
        from types import SimpleNamespace as N
        from traslado.legacy import _map
        from traslado.render import render
        import base64
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        text = lambda value: N(kind="text", text=value)
        paragraph = lambda items: N(kind="paragraph", content=items)
        image = N(kind="image", alt="Figura", source=N(kind="asset", asset_id=0))
        source = N(notes=[], assets=[N(id=0, media_type="image/png", origin_part="image.png", data=png)],
                   blocks=[paragraph([text("Antes "), image, text(" depois")]),
                           N(kind="block_quote", blocks=[paragraph([text("Citação")])]),
                           N(kind="code_block", text="```\nexemplo", lang="text"), N(kind="rule"),
                           N(kind="math", text="x=1"), paragraph([N(kind="checkbox", checked=True),
                           N(kind="line_break"), N(kind="math", text="y=2")])])
        with scratch() as root:
            for enabled in (True, False):
                folder = root / str(enabled)
                folder.mkdir()
                doc = Document("fake.rtf", "hash")
                _map(source, folder, Options(images=enabled), doc)
                md = render(doc)
                self.assertIn("> Citação", md)
                self.assertIn("````text\n```\nexemplo\n````", md)
                self.assertIn("$$\nx=1\n$$", md)
                self.assertIn("[x] \n$y=2$", md)
                self.assertEqual((folder / "assets/legacy-1.png").is_file(), enabled)
                self.assertEqual("image_semantics_unverified" in doc.pages[0].warnings, enabled)
                if enabled:
                    self.assertIn("Antes ![Figura](assets/legacy-1.png) depois", md)
                    self.assertEqual(doc.pages[0].metrics["legacy_assets"][0]["origin_part"], "image.png")
            with self.assertRaises(ConversionError) as caught:
                _map(N(blocks=[paragraph([text("x"*1000)])], notes=[], assets=[]), root,
                     Options(max_output_bytes=100), Document("big.rtf", "hash"))
            self.assertEqual(caught.exception.code, "resource_limit")

    def test_exception_mapping(self):
        from anydoc import _anydoc as native
        from traslado.legacy import extract
        cases = [("UnsupportedError", "unsupported"), ("MalformedError", "malformed"),
                 ("EncryptedError", "encrypted"), ("ResourceLimitError", "resource_limit"),
                 ("MissingPartError", "missing_part"), ("ConvertError", "malformed")]
        with scratch() as root:
            for name, code in cases:
                with self.subTest(name=name), patch.object(native, "to_document", side_effect=getattr(native, name)("failure")):
                    with self.assertRaises(ConversionError) as caught:
                        extract(FIXTURES / "publico.rtf", root, Options(legacy=True), Document("fixture", "hash"))
                    self.assertEqual(caught.exception.code, code)
            class NeedsOcr(native.NeedsOcrError):
                pages = [1, 3]
            with patch.object(native, "to_document", side_effect=NeedsOcr("scanned")):
                with self.assertRaises(ConversionError) as caught:
                    extract(FIXTURES / "publico.rtf", root, Options(legacy=True), Document("fixture", "hash"))
                self.assertEqual(caught.exception.code, "needs_ocr")
                self.assertEqual(caught.exception.data, {"error_pages": [1, 3]})
            with patch.object(native, "to_document", side_effect=OSError("read")):
                with self.assertRaises(ConversionError) as caught:
                    extract(FIXTURES / "publico.rtf", root, Options(legacy=True), Document("fixture", "hash"))
                self.assertEqual(caught.exception.code, "io")

    def test_200_deterministic_mutations_per_fixture(self):
        # Every case uses the real supervisor + contained worker, with independent
        # memory/output/time ceilings. Four concurrent supervisors bound total load.
        with scratch() as root:
            failures = []
            def run(case):
                fmt, index, payload = case
                folder = root / (fmt + str(index))
                folder.mkdir()
                source = folder / ("mutant." + fmt)
                source.write_bytes(payload)
                result = execute(source, folder / "out", Options(legacy=True, job_timeout=30,
                                 max_memory_mb=512, max_output_bytes=2_000_000,
                                 max_archive_bytes=4_000_000, max_cells=10000))
                try:
                    if result["output"]:
                        self.assertLessEqual(sum(p.stat().st_size for p in Path(result["output"]).rglob("*") if p.is_file()), 2_000_000)
                    if (not (result["status"] in {"complete", "partial"} or
                             result.get("error_code") not in {None, "internal"})
                            or result["duration_seconds"] >= 60):
                        failures.append({"format": fmt, "case": index, "result": result})
                        retained = root.parent / "e4-mutant-failures"
                        retained.mkdir(exist_ok=True)
                        (retained / (fmt + str(index) + "." + fmt)).write_bytes(payload)
                finally:
                    shutil.rmtree(folder)
                return fmt, result["status"], result.get("error_code")
            cases = []
            for fmt in FORMATS:
                with self.subTest(format=fmt):
                    source = fixture(fmt)
                if not (FIXTURES / ("publico." + fmt)).is_file():
                    continue
                original = source.read_bytes()
                rng = random.Random(204024)
                for index in range(200):
                    data = bytearray(original)
                    at = rng.randrange(len(data))
                    width = rng.randint(1, min(64, len(data) - at))
                    if index % 4 == 0:
                        for j in range(at, at + width):
                            data[j] ^= rng.randrange(1, 256)
                    elif index % 4 == 1:
                        del data[at:]
                    elif index % 4 == 2:
                        data[at:at] = data[at:at+width]
                    else:
                        data[at:at+width] = b"\0" * width
                    cases.append((fmt, index, bytes(data)))
            with ThreadPoolExecutor(max_workers=4) as pool:
                counts = Counter(pool.map(run, cases))
            (root.parent / "e4-mutation-results.json").write_text(
                json.dumps({str(k): v for k, v in sorted(counts.items(), key=str)}, indent=2), "utf-8")
            self.assertFalse(failures, failures)


if __name__ == "__main__":
    unittest.main()
