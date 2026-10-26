"""Output UX contracts exercised through actual conversion and publication."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from freoa_v6.model import Options
from freoa_v6.runner import execute


class OutputLayoutRC4(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="freoa-output-contract-")
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "Destino escolhido"

    def tearDown(self):
        self.temp.cleanup()

    def text(self, name, value):
        source = self.root / "Originais" / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(value, encoding="utf-8")
        return source

    def completed(self, source, options=None):
        original = source.read_bytes()
        existing_private = {p.name: p.read_bytes() for p in self.output.iterdir()
                            if p.is_file() and p.name.startswith(".freoa-")} if self.output.exists() else {}
        result = execute(source, self.output, options or Options())
        self.assertEqual(result["status"], "complete", result)
        package = Path(result["output"])
        self.assertEqual(package.parent.resolve(), self.output.resolve())
        self.assertEqual(source.read_bytes(), original)
        document = json.loads((package / "document.json").read_text(encoding="utf-8"))
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(original).hexdigest()
        self.assertEqual(document["source_sha256"], digest)
        self.assertEqual(manifest["source_sha256"], digest)
        self.assertEqual(document["source_name"], source.name)
        self.assertEqual(manifest["source_name"], source.name)
        for entry in manifest["files"]:
            file = package / entry["path"]
            self.assertTrue(file.resolve().is_relative_to(package.resolve()))
            self.assertEqual(file.stat().st_size, entry["bytes"])
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), entry["sha256"])
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()
                          if p.is_file() and p.name.startswith(".freoa-")}, existing_private)
        self.assertFalse(any(p.name.startswith(".freoa-work-") for p in self.output.iterdir()))
        return package

    def snapshot(self, package):
        return {p.relative_to(package).as_posix(): p.read_bytes()
                for p in package.rglob("*") if p.is_file()}

    def test_document_named_subfolder_inside_chosen_destination(self):
        source = self.text("DOC1.txt", "ANTES\nConteúdo íntegro: 12345\nDEPOIS")
        package = self.completed(source)
        self.assertEqual(package, self.output / "DOC1")
        self.assertIn("Conteúdo íntegro: 12345", (package / "document.md").read_text(encoding="utf-8"))
        self.assertEqual({p.name for p in self.output.iterdir()}, {"DOC1"})

    def test_unicode_document_name_remains_recognizable(self):
        source = self.text("Parecer jurídico – João e ação 2026.txt", "UNICODE_SOURCE_SENTINEL")
        package = self.completed(source)
        self.assertEqual(package.name, "Parecer jurídico – João e ação 2026")

    def test_same_name_new_content_and_identical_rerun_preserve_predecessors(self):
        first = self.text("volume1/Parecer.txt", "FIRST_CONTENT_SENTINEL")
        second = self.text("volume2/Parecer.txt", "SECOND_CONTENT_SENTINEL")
        package1 = self.completed(first)
        before1 = self.snapshot(package1)
        package2 = self.completed(second)
        before2 = self.snapshot(package2)
        package3 = self.completed(second)
        self.assertEqual([p.name for p in (package1, package2, package3)],
                         ["Parecer", "Parecer (2)", "Parecer (3)"])
        self.assertEqual(self.snapshot(package1), before1)
        self.assertEqual(self.snapshot(package2), before2)
        self.assertIn("SECOND_CONTENT_SENTINEL", (package3 / "document.md").read_text(encoding="utf-8"))

    def test_existing_file_and_empty_folder_are_never_replaced(self):
        source = self.text("Protegido.txt", "NEW_CONTENT_SENTINEL")
        self.output.mkdir()
        (self.output / "Protegido").write_bytes(b"USER_FILE_IMMUTABLE")
        (self.output / "Protegido (2)").mkdir()
        package = self.completed(source)
        self.assertEqual(package.name, "Protegido (3)")
        self.assertEqual((self.output / "Protegido").read_bytes(), b"USER_FILE_IMMUTABLE")
        self.assertEqual(list((self.output / "Protegido (2)").iterdir()), [])

    def test_preexisting_reservation_is_preserved_and_name_skipped(self):
        source = self.text("Reservado.txt", "RESERVATION_SENTINEL")
        self.output.mkdir()
        stale = self.output / ".freoa-reserva-Reservado"
        stale.write_bytes(b"PREEXISTING_RESERVATION_DO_NOT_DELETE")
        package = self.completed(source)
        self.assertEqual(package.name, "Reservado (2)")
        self.assertEqual(stale.read_bytes(), b"PREEXISTING_RESERVATION_DO_NOT_DELETE")

    @unittest.skipUnless(os.name == "nt", "Windows extended-path reserved input")
    def test_long_reserved_name_stays_within_readable_folder_limit(self):
        parent = self.root / "Originais"
        parent.mkdir()
        source = Path("\\\\?\\" + str(parent / ("CON." + "é" * 100 + ".txt")))
        source.write_bytes(b"LONG_RESERVED_SOURCE_SENTINEL")
        package = self.completed(source)
        self.assertLessEqual(len(package.name), 80)
        self.assertNotEqual(package.name.split(".")[0].upper(), "CON")

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive publication")
    def test_windows_case_collision_does_not_replace_first_document(self):
        first = self.text("one/PARECER.txt", "UPPER_SENTINEL")
        second = self.text("two/parecer.txt", "LOWER_SENTINEL")
        package1 = self.completed(first)
        before = self.snapshot(package1)
        package2 = self.completed(second)
        self.assertEqual(package2.name, "parecer (2)")
        self.assertEqual(self.snapshot(package1), before)

    def test_long_unicode_name_is_shortened_without_losing_source_provenance(self):
        source = self.text("Parecer sobre obrigações " + "é" * 100 + ".txt", "LONG_NAME_SENTINEL")
        package = self.completed(source)
        self.assertLessEqual(len(package.name), 80)
        self.assertTrue(package.name.startswith("Parecer sobre obrigações"))
        self.assertNotEqual(package.name, source.stem)
        self.assertIn("é", package.name)

    def test_long_volume_names_retain_distinct_edition_tails(self):
        prefix = "Pontes de Miranda, Francisco Cavalcanti , Dez Anos de Pareceres, Francisco Alves , "
        first = self.text(prefix + "1976, v 6.txt", "VOLUME_SIX_SENTINEL")
        second = self.text(prefix + "1976, v 7.txt", "VOLUME_SEVEN_SENTINEL")
        package1 = self.completed(first)
        package2 = self.completed(second)
        self.assertLessEqual(len(package1.name), 80)
        self.assertLessEqual(len(package2.name), 80)
        self.assertTrue(package1.name.endswith("1976, v 6"), package1.name)
        self.assertTrue(package2.name.endswith("1976, v 7"), package2.name)
        self.assertFalse(package2.name.endswith(" (2)"))

    def test_trailing_stem_space_is_normalized_for_windows(self):
        source = self.text("Parecer .txt", "TRAILING_SPACE_SENTINEL")
        package = self.completed(source)
        self.assertEqual(package.name, "Parecer")

    @unittest.skipUnless(os.name == "nt", "Windows extended-path reserved input")
    def test_reserved_windows_source_name_gets_safe_contained_folder(self):
        parent = self.root / "Originais"
        parent.mkdir()
        # Extended paths let the fixture represent a real legacy/downloaded file.
        source = Path("\\\\?\\" + str(parent / "CON.txt"))
        source.write_bytes(b"RESERVED_SOURCE_SENTINEL")
        package = self.completed(source)
        self.assertNotEqual(package.name.split(".")[0].upper(), "CON")
        self.assertFalse(package.name.endswith((" ", ".")))
        self.assertFalse(any(c in package.name for c in '<>:"/\\|?*'))
        self.assertIn("RESERVED_SOURCE_SENTINEL", (package / "document.md").read_text(encoding="utf-8"))

    def test_concurrent_same_document_publications_preserve_both_packages(self):
        source = self.text("Simultâneo.txt", "CONCURRENT_DOCUMENT_SENTINEL")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: execute(source, self.output, Options()), range(2)))
        for result in results:
            self.assertEqual(result["status"], "complete", result)
        packages = [Path(result["output"]) for result in results]
        self.assertEqual({p.name for p in packages}, {"Simultâneo", "Simultâneo (2)"})
        for package in packages:
            self.assertIn("CONCURRENT_DOCUMENT_SENTINEL", (package / "document.md").read_text(encoding="utf-8"))
        self.assertFalse(any(p.name.startswith(".freoa-") for p in self.output.iterdir()))

    def test_cancelled_snapshot_leaves_no_published_folder_or_temporary_data(self):
        source = self.text("Cancelar.txt", "CANCEL_SOURCE " * 100)
        original = source.read_bytes()
        calls = 0
        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 2
        result = execute(source, self.output, Options(), cancel=cancel)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertIsNone(result["output"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_immediately_before_publication_removes_owned_reservation(self):
        source = self.text("Cancelar publicação.txt", "PREPUBLICATION_CANCEL_SENTINEL")
        original = source.read_bytes()
        def cancel():
            return self.output.exists() and any(
                p.name.startswith(".freoa-reserva-") for p in self.output.iterdir())
        result = execute(source, self.output, Options(), cancel=cancel)
        self.assertEqual(result["status"], "cancelled", result)
        self.assertIsNone(result["output"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_budget_failure_preserves_existing_user_document(self):
        source = self.text("Excessivo.txt", "BUDGET_SOURCE " * 100)
        original = source.read_bytes()
        self.output.mkdir()
        protected = self.output / "arquivo-do-usuário.md"
        protected.write_bytes(b"PRESERVE_EXISTING_OUTPUT")
        result = execute(source, self.output, Options(max_output_bytes=100))
        self.assertEqual(result["status"], "failed", result)
        self.assertIsNone(result["output"])
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(protected.read_bytes(), b"PRESERVE_EXISTING_OUTPUT")
        self.assertEqual(list(self.output.iterdir()), [protected])


if __name__ == "__main__":
    unittest.main()
