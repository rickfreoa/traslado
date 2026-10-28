# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Regressões dos 14 achados da revisão E5; sem rede por padrão."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from traslado import __version__, lifecycle, platform as plat, revocation, runtime, ocr
from tests.test_lifecycle_e5 import synthetic_certificate_bundle

ROOT = Path(__file__).resolve().parents[1]
WHEEL = f"traslado-{__version__}-py3-none-any.whl"
DIGESTS = {WHEEL: "a" * 64, "requirements-core.hashes.lock": "b" * 64}


class CorrectionsE5(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="e5-corrections-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "managed"

    def setup(self, release="2.0.0_a", **kw):
        py = plat.release_python(self.root, release)
        py.parent.mkdir(parents=True, exist_ok=True)
        if not py.exists():
            py.write_bytes(b"interpreter sentinel")
        options = dict(root=self.root, release_id=release, profile="Core", promote=True,
                       no_shortcut=True, skip_ocr=True, allow_offline_promotion=True,
                       env={"TRASLADO_PROMOCAO_OFFLINE": "teste"},
                       doctor_fn=lambda *a: {"capabilities": {}}, fixture_fn=lambda: True)
        options.update(kw)
        return lifecycle.setup_release(**options)

    def test_01_empty_incomplete_and_all_asset_digests(self):
        for values in ({}, {WHEEL: "a"*64}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, "incompleto"):
                lifecycle.validate_bootstrap_digests(values, __version__, "Core", [])
        self.assertEqual(lifecycle.validate_bootstrap_digests(DIGESTS, __version__, "Core", []), DIGESTS)
        release = dict(draft=False, prerelease=False, tag_name=f"v{__version__}", assets=[])
        for values in ({}, DIGESTS):
            with self.assertRaises(revocation.RevocationError):
                revocation.verify_release_channel(release, __version__, values)
        release["assets"] = [{"name": k, "digest": "sha256:"+v} for k, v in DIGESTS.items()]
        revocation.verify_release_channel(release, __version__, DIGESTS)
        release["assets"][1]["digest"] = "sha256:"+"c"*64
        with self.assertRaises(revocation.RevocationError):
            revocation.verify_release_channel(release, __version__, DIGESTS)

    def test_03_certificate_required_and_san_exact(self):
        uri = "https://github.com/rickfreoa/traslado/.github/workflows/revocation.yml@refs/heads/main"
        free = dict(signer_workflow="rickfreoa/traslado/.github/workflows/revocation.yml", ref="refs/heads/main",
                    dsseEnvelope={"payload": base64.b64encode(uri.encode()).decode()})
        self.assertFalse(revocation._bundle_matches_identity(free, revocation.DEFAULT_REPO))
        cert = synthetic_certificate_bundle(uri)
        self.assertTrue(revocation._bundle_matches_identity(cert, revocation.DEFAULT_REPO))
        for bad in (uri+"-evil", uri.replace("revocation", "release"), "https://evil.invalid/"+uri):
            self.assertFalse(revocation._bundle_matches_identity(synthetic_certificate_bundle(bad), revocation.DEFAULT_REPO))
        invalid = {"verificationMaterial": {"certificate": {"rawBytes": base64.b64encode(uri.encode()).decode()}}}
        self.assertFalse(revocation._bundle_matches_identity(invalid, revocation.DEFAULT_REPO))
        release_uri = "https://github.com/rickfreoa/traslado/.github/workflows/release.yml@refs/tags/v2.0.0"
        self.assertTrue(revocation._bundle_matches_identity(synthetic_certificate_bundle(release_uri),
            revocation.DEFAULT_REPO, ".github/workflows/release.yml", "refs/tags/v2.0.0"))

    def test_04_offline_requires_both_flag_and_context(self):
        for flag, env in ((False, {"TRASLADO_PROMOCAO_OFFLINE": "teste"}), (True, {}), (True, {"TRASLADO_PROMOCAO_OFFLINE": "production"})):
            with self.subTest(flag=flag, env=env), self.assertRaisesRegex(ValueError, "Promoção recusada"):
                self.setup(allow_offline_promotion=flag, env=env, from_dir=Path(self.tmp.name))
            self.assertIsNone(plat.read_current(self.root))
        for reason in ("rascunho", "teste"):
            result = self.setup("2.0.0_"+reason, env={"TRASLADO_PROMOCAO_OFFLINE": reason})
            self.assertEqual(result["gates"]["revocation"], "offline")
            self.assertEqual(result["gates"]["offline_reason"], reason)

    def test_05_real_uninstall_preserves_deep_internal_data_both_modes(self):
        for keep in (False, True):
            with self.subTest(keep=keep):
                data = self.root / "releases/user/files"
                self.setup("2.0.0_"+str(keep), data_root=data)
                sentinel = data / "sub/document.md"
                sentinel.parent.mkdir(exist_ok=True)
                sentinel.write_bytes(b"user data must survive")
                extra = self.root / "releases/user/disposable"
                extra.write_bytes(b"remove")
                lifecycle.uninstall_managed(root=self.root, yes=True, keep_data=keep, env={},
                                           platform_name="linux", home=Path(self.tmp.name))
                self.assertEqual(sentinel.read_bytes(), b"user data must survive")
                self.assertFalse(extra.exists())
                self.assertFalse((self.root / "current.json").exists())
                self.assertFalse((self.root / "releases" / ("2.0.0_"+str(keep))).exists())

    def test_06_shortcuts_after_promotion_failure_and_rollback(self):
        seen = []
        def shortcut(target, **kwargs):
            active = plat.read_current(self.root)
            self.assertIsNotNone(active)
            self.assertEqual(Path(target), plat.release_python(self.root, active["release_id"]))
            seen.append(active["release_id"])
        with patch.object(plat, "create_shortcut", side_effect=shortcut):
            self.setup("2.0.0_a", no_shortcut=False)
            before = (self.root / "current.json").read_bytes()
            with self.assertRaises(ValueError):
                self.setup("2.0.0_bad", no_shortcut=False, fixture_fn=lambda: False)
            self.assertEqual((self.root / "current.json").read_bytes(), before)
            self.assertEqual(seen, ["2.0.0_a"])
            self.setup("2.0.0_b", no_shortcut=False)
            plat.rollback(self.root)
            self.assertEqual(seen, ["2.0.0_a", "2.0.0_b", "2.0.0_a"])

    def test_06_rollback_updates_shortcuts_created_only_by_newer_release(self):
        seen = []
        def shortcut(target, **kwargs):
            active = plat.read_current(self.root)
            self.assertEqual(Path(target), plat.release_python(self.root, active["release_id"]))
            seen.append(active["release_id"])
        with patch.object(plat, "create_shortcut", side_effect=shortcut):
            self.setup("2.0.0_a", no_shortcut=True)
            self.setup("2.0.0_b", no_shortcut=False)
            self.assertEqual(seen, ["2.0.0_b"])
            plat.rollback(self.root)
            self.assertEqual(seen, ["2.0.0_b", "2.0.0_a"])

    def test_07_workflow_isolated_emitter_smoke(self):
        target = Path(self.tmp.name) / "emitter"
        # Actual local package install, without dependencies or network build isolation.
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(target)], check=True, capture_output=True)
        py = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install = subprocess.run([sys.executable, "-m", "pip", "--python", str(py), "install", "--no-deps", "--no-build-isolation", str(ROOT)], capture_output=True, text=True)
        self.assertEqual(install.returncode, 0, install.stdout+install.stderr)
        output = Path(self.tmp.name) / "revoked.json"
        smoke = subprocess.run([str(py), "-I", "-m", "traslado.revocation", "--input", str(ROOT / "revocation/revoked-list.json"), "--previous-sequence", "41", "--output", str(output)], cwd=target, capture_output=True, text=True)
        self.assertEqual(smoke.returncode, 0, smoke.stdout+smoke.stderr)
        self.assertEqual(json.loads(output.read_text())["sequence"], 42)

    def test_08_09_workflow_serialization_errors_and_latest_contract(self):
        workflow = (ROOT / ".github/workflows/revocation.yml").read_text(encoding="utf-8")
        self.assertIn("group: traslado-revocation", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("404) PREV_SEQ=0", workflow)
        self.assertIn('HTTP $STATUS', workflow)
        self.assertIn('exit 1 ;;', workflow)
        self.assertIn("--latest=false", workflow)
        self.assertEqual(workflow.count("gh api repos/rickfreoa/traslado/releases/latest"), 2)
        self.assertLess(workflow.index("pip install --no-deps ."), workflow.index("python -I -m traslado.revocation"))

    def test_10_deferred_script_body(self):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        ps = (ROOT / "install.ps1").read_text(encoding="utf-8")
        self.assertEqual(sh.splitlines()[-1], 'main "$@"')
        self.assertEqual(next(l for l in sh.splitlines() if l and not l.startswith('#')), 'main() {')
        self.assertEqual(ps.splitlines()[-1], '& $InstallMain @args')
        self.assertEqual(next(l.strip() for l in ps.splitlines() if l.strip() and not l.startswith('#')), '$InstallMain = {')

    def test_12_private_tessdata_external_tesseract_all_platforms(self):
        release = self.root / "releases/2.0.0_a"
        data = release / "ocr/tessdata"
        data.mkdir(parents=True)
        exe = Path(self.tmp.name) / "external/tesseract"
        exe.parent.mkdir()
        exe.write_bytes(b"inert")
        plat._atomic(self.root / "current.json", {"release_id": release.name, "ocr": {"tesseract": str(exe)}})
        # The recorded executable takes precedence even if an old private exe remains.
        (release / "ocr/tesseract.exe").write_bytes(b"old inert executable")
        with patch.object(runtime, "managed_root", return_value=release), patch.object(runtime.shutil, "which", return_value=str(exe)):
            for system in ("linux", "darwin"):
                with patch.object(sys, "platform", system):
                    self.assertEqual(ocr.find_tesseract(), str(exe))
                    self.assertEqual(runtime.managed_tessdata_for(str(exe)), str(data))
            (self.root / "current.json").unlink()
            (release / "ocr/tesseract.exe").unlink()
            self.assertEqual(runtime.managed_tesseract(), str(exe))
            self.assertEqual(runtime.managed_tessdata_for(str(exe)), str(data))

    def test_13_output_status_errors_and_every_artifact(self):
        output = Path(self.tmp.name) / "output"
        output.mkdir()
        files = ["document.md", "document.json", "manifest.json", "LEIA-AVISOS.txt", "chunks/chunks.jsonl"]
        for name in files:
            path = output / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("fixture")
        def gate(result, **kw):
            return lifecycle.run_fixture_gate(profile="Full", execute_fn=lambda *a: result, **kw)
        for status in ("complete", "partial"):
            good = {"status": status, "output": str(output)}
            self.assertTrue(gate(good, chunks=True))
            self.assertFalse(gate({"status": status}))
            self.assertFalse(gate(dict(good, error_code="unsupported")))
            for name in files:
                path = output / name
                path.unlink()
                self.assertFalse(gate(good, chunks=True), name)
                path.write_text("fixture")
        self.assertFalse(gate({"status": "ok", "output": str(output)}))

    def test_13_real_full_fixture_preventive_docx_warning(self):
        self.assertTrue(lifecycle.run_fixture_gate(profile="Full", chunks=True))

    def test_04_low_level_promotion_cannot_reuse_unapproved_offline_candidate(self):
        self.setup(promote=False, allow_offline_promotion=False)
        with self.assertRaises(ValueError):
            plat.promote(self.root)
        self.assertIsNone(plat.read_current(self.root))

    def test_14_idempotency_integrity_and_referenced_releases(self):
        a = self.setup("2.0.0_a")
        before = (self.root / "current.json").read_bytes()
        self.assertTrue(lifecycle.active_release_matches(self.root, __version__, "Core"))
        self.setup("2.0.0_a")
        self.assertEqual((self.root / "current.json").read_bytes(), before)
        a_py = plat.release_python(self.root, "2.0.0_a")
        a_bytes = a_py.read_bytes()
        self.setup("2.0.0_b")
        with self.assertRaisesRegex(ValueError, "referenciada"):
            self.setup("2.0.0_a", fixture_fn=lambda: False)
        self.assertEqual(a_py.read_bytes(), a_bytes)
        active = plat.release_python(self.root, "2.0.0_b")
        active.write_bytes(b"corrupted")
        self.assertFalse(lifecycle.active_release_matches(self.root, __version__, "Core"))
        self.assertEqual(plat.read_current(self.root)["previous_release_id"], a["release_id"])


@unittest.skipUnless(os.name == "nt" and os.environ.get("TRASLADO_E2E_REAL") == "1",
                     "bootstrap real exige Windows e TRASLADO_E2E_REAL=1, rede e uv fora do sandbox")
class RealBootstrapE5(unittest.TestCase):
    def test_windows_real_uv_hashed_locks(self):
        evidence = ROOT / "docs/evidencias-e5/bootstrap-real-windows.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        # The integrator runs this outside the sandbox. No uv or pip mocks.
        with evidence.open("w", encoding="utf-8") as log, tempfile.TemporaryDirectory(prefix="traslado-e5-real-") as temp:
            base = Path(temp)
            artifacts = base / "artifacts"
            build = [sys.executable, str(ROOT / "scripts/build_release.py"), "--permitir-sujo", "--dist", str(artifacts)]
            log.write("BUILD: " + repr(build) + "\n"); log.flush()
            subprocess.run(build, cwd=ROOT, check=True, stdout=log, stderr=subprocess.STDOUT)
            # Local un-published artifacts have no attestations. Exclude gh from this
            # offline draft test; gh enforcement is exercised by separate unit regressions.
            env = dict(os.environ)
            env.pop("TRASLADO_TEST_UV", None)
            win = Path(env.get("SystemRoot", r"C:\Windows"))
            env["PATH"] = os.pathsep.join(str(p) for p in (win / "System32", win, win / "System32/WindowsPowerShell/v1.0"))
            env.update(TRASLADO_PROMOCAO_OFFLINE="teste", TRASLADO_LEGACY_ROOT=str(base / "no-legacy"),
                       TRASLADO_SHORTCUT_DESKTOP_DIR=str(base / "desktop"),
                       TRASLADO_SHORTCUT_START_MENU_DIR=str(base / "startmenu"))
            command = [str(win / "System32/WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(artifacts / "install.ps1"), "-FromDir", str(artifacts), "-Root", str(base / "managed"), "-Profile", "Full", "-SkipOCR", "-AllowOfflinePromotion"]
            log.write("BOOTSTRAP REAL: " + repr(command) + "\nLocks: --require-hashes --only-binary :all:\n"); log.flush()
            subprocess.run(command, cwd=ROOT, env=env, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            current = plat.read_current(base / "managed")
            self.assertEqual(current["gates"], {"doctor": True, "fixture": True, "revocation": "offline", "offline_reason": "teste"})
            py = current["python"]
            subprocess.run([py, "-I", "-c", "import fitz,PIL,docx,openpyxl,pptx; print('FULL DEPENDENCIES INSTALLED')"], check=True, stdout=log, stderr=subprocess.STDOUT)
            self.assertTrue(list((base / "desktop").glob("Traslado*")))
            self.assertTrue(list((base / "startmenu").rglob("Traslado*")))
            first = (base / "managed/current.json").read_bytes()
            count = len(list((base / "managed/releases").iterdir()))
            subprocess.run(command, cwd=ROOT, env=env, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            self.assertEqual((base / "managed/current.json").read_bytes(), first)
            self.assertEqual(len(list((base / "managed/releases").iterdir())), count)
            log.write("PASS: real uv, Full locks, fixture, temporary shortcuts, idempotent reinstall\n")


if __name__ == "__main__":
    unittest.main()
