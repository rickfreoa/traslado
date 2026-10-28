# SPDX-License-Identifier: AGPL-3.0-or-later
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from traslado.cli import main


class CLIContracts(unittest.TestCase):
    def test_actual_complete_partial_failure_exit_matrix(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cases = [("regular.txt", "PAYMENT_42", 0, "complete"),
                     ("table.html", "<table><tr><td>12345</td></tr></table>", 3, "partial"),
                     ("legacy.txt", b"Servi\xe7o", 2, "failed")]
            for name, payload, expected, status in cases:
                source = root / name
                source.write_bytes(payload.encode("utf-8") if isinstance(payload, str) else payload)
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = main(["convert", str(source), "-o", str(root / "out")])
                self.assertEqual(code, expected, name)
                self.assertIn('"status": "' + status + '"', stdout.getvalue(), name)

