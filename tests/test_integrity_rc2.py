from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from traslado import runner, worker
from traslado.model import Options


class IntegrityRC2(unittest.TestCase):
    def test_worker_rejects_snapshot_with_forged_digest(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = root / "source.txt"
            snapshot.write_text("FORGED_12345", encoding="utf-8")
            package = root / "package"
            package.mkdir()
            request = root / "request.json"
            request.write_text(json.dumps({"source_name": "sample.txt", "sha256": "0" * 64,
                "snapshot": str(snapshot), "workspace": str(package), "options": asdict(Options())}))
            with patch.object(sys, "argv", ["worker", str(request)]):
                self.assertEqual(worker.main(), 2)
            self.assertFalse((package / "document.md").exists())
            self.assertIn("hash", (package / "error.json").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Mandatory snapshot lock is Windows-specific")
    def test_effective_worker_snapshot_cannot_be_modified_before_parse(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.txt"
            source.write_text("ORIGINAL_12345", encoding="utf-8")
            real = runner.run_bounded
            denied = []
            def attempt(argv, *args, **kwargs):
                request = json.loads(Path(argv[-1]).read_text())
                try:
                    Path(request["snapshot"]).write_text("FORGED_98765", encoding="utf-8")
                except PermissionError:
                    denied.append(True)
                return real(argv, *args, **kwargs)
            with patch.object(runner, "run_bounded", side_effect=attempt):
                result = runner.execute(source, root / "out")
            self.assertEqual(denied, [True])
            self.assertEqual(result["status"], "complete", result)
            document = json.loads((Path(result["output"]) / "document.json").read_text())
            self.assertEqual(document["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertIn("ORIGINAL_12345", json.dumps(document))
            self.assertNotIn("FORGED_98765", json.dumps(document))

    @unittest.skipUnless(os.name == "nt", "Windows publication denial")
    def test_transient_publication_denial_retains_content(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.txt"
            source.write_text("PAYMENT_42")
            real = Path.rename
            calls = []
            def rename(path, target):
                if path.name == "package":
                    calls.append(target)
                    if len(calls) < 3:
                        raise OSError(0, "transient access denied", str(path), 5)
                return real(path, target)
            with patch.object(Path, "rename", rename):
                result = runner.execute(source, root / "out")
            self.assertEqual(result["status"], "complete", result)
            self.assertEqual(len(calls), 3)
            self.assertEqual(len(set(calls)), 1)
            self.assertIn("PAYMENT_42", (Path(result["output"]) / "document.md").read_text())

    @unittest.skipUnless(os.name == "nt", "Windows publication denial")
    def test_mutation_during_publication_retry_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source.txt"
            source.write_text("ORIGINAL_123")
            def rename(path, target):
                (path / "document.md").write_text("FORGED_987")
                raise OSError(0, "transient access denied", str(path), 5)
            with patch.object(Path, "rename", rename):
                result = runner.execute(source, root / "out")
            self.assertEqual(result["status"], "failed", result)
            self.assertIsNone(result["output"])
            self.assertIn("mudou", result["error"])
            self.assertFalse(list((root / "out").glob("document-*")))
