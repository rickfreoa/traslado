# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Regressões dos 14 achados da revisão E5; sem rede por padrão."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
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
try:
    from tests.test_lifecycle_e5 import synthetic_certificate_bundle
except ImportError:  # suíte rodando de cópia dos testes, sem o pacote tests
    from test_lifecycle_e5 import synthetic_certificate_bundle

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
        # Instalação local real, sem dependências nem rede: o wheel é construído sem isolamento pelo
        # interpretador dos testes (setuptools do lock de testes; PEP 639 exige >= 77) e instalado num venv limpo.
        versao = subprocess.run([sys.executable, "-c", "import setuptools; print(setuptools.__version__)"], capture_output=True, text=True)
        if versao.returncode != 0 or int(versao.stdout.strip().split(".")[0]) < 77:
            # Na CI o lock de testes fixa setuptools 82.0.1: ali a ausência é defeito, não ambiente.
            self.assertFalse(os.environ.get("GITHUB_ACTIONS"), "setuptools >= 77 ausente na CI: " + versao.stdout + versao.stderr[-200:])
            self.skipTest("setuptools ausente ou anterior a 77 (PEP 639): " + (versao.stdout.strip() or versao.stderr[-200:]))
        wheels = Path(self.tmp.name) / "wheels"
        build = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(wheels), str(ROOT)], capture_output=True, text=True)
        self.assertEqual(build.returncode, 0, build.stdout+build.stderr)
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True, capture_output=True)
        py = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        limpo = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # o venv novo não pode "já ter" o traslado
        install = subprocess.run([sys.executable, "-m", "pip", "--python", str(py), "install", "--no-deps", "--ignore-installed",
                                  *map(str, wheels.glob("traslado-*.whl"))], capture_output=True, text=True, env=limpo)
        self.assertEqual(install.returncode, 0, install.stdout+install.stderr)
        output = Path(self.tmp.name) / "revoked.json"
        smoke = subprocess.run([str(py), "-I", "-m", "traslado.revocation", "--input", str(ROOT / "revocation/revoked-list.json"), "--previous-sequence", "41", "--output", str(output)], cwd=target, capture_output=True, text=True)
        self.assertEqual(smoke.returncode, 0, smoke.stdout+smoke.stderr)
        self.assertEqual(json.loads(output.read_text())["sequence"], 42)

    @unittest.skipUnless((ROOT / ".github/workflows/revocation.yml").is_file(), "workflows ausentes no pacote extraído")
    def test_08_09_workflow_serialization_errors_and_latest_contract(self):
        workflow = (ROOT / ".github/workflows/revocation.yml").read_text(encoding="utf-8")
        self.assertIn("group: traslado-revocation", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("404) PREV_SEQ=0", workflow)
        self.assertIn('HTTP $STATUS', workflow)
        self.assertIn('exit 1 ;;', workflow)
        self.assertIn("--latest=false", workflow)
        self.assertEqual(workflow.count("gh api repos/rickfreoa/traslado/releases/latest"), 1)
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

    @unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("docx", "fitz", "openpyxl", "pptx", "PIL")), "fixture Full exige o perfil Full")
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


    def test_n2_ocr_only_from_packaged_wheel(self):
        local = Path(self.tmp.name) / "untrusted"
        release = self.root / "releases/test"
        for directory in (local, release, release / "scripts"):
            directory.mkdir(parents=True, exist_ok=True)
            for name in ("ocr-assets.lock.json", "bootstrap_ocr.ps1"):
                (directory / name).write_text("malicious local replacement")
        packaged = Path(lifecycle.__file__).parent / "assets"
        self.assertEqual(lifecycle._find_ocr_lock(release, local), packaged / "ocr-assets.lock.json")
        self.assertEqual(lifecycle._find_bootstrap_ps1(release, local), packaged / "bootstrap_ocr.ps1")
        for name in ("install.ps1", "install.sh"):
            self.assertNotIn("localOcrLock", (ROOT / name).read_text())
            self.assertNotIn("LOCAL_OCR_LOCK=", (ROOT / name).read_text())

    def test_n3_update_preserves_config_both_platforms(self):
        config = self.setup(profile="Core", extras="legacy", languages="por,fra", no_shortcut=True)
        config["shortcuts"].update(home=str(Path(self.tmp.name) / "home"), dirs={"desktop_dir": str(self.root / "desktop")})
        plat._atomic(self.root / "current.json", config)
        for system, script, flags in (("win32", "install.ps1", ("-Profile", "-Extras", "-Languages", "-DataRoot", "-NoShortcut")),
                                      ("linux", "install.sh", ("--profile", "--extras", "--languages", "--data-root", "--no-shortcut"))):
            raw = b"installer"
            sums = (hashlib.sha256(raw).hexdigest()+"  "+script+"\n").encode()
            calls = []
            def run(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0)
            with patch.object(revocation, "verify_promotion", return_value={"status": "ok"}):
                lifecycle.update_release(root=self.root, platform_name=system, gh_cmd="gh-stub", env={"TRASLADO_DATA_ROOT": "bad", "TRASLADO_SHORTCUT_ICONS_DIR": "bad"},
                    fetch_json=lambda url: {"tag_name": "v2.0.1"}, fetch_bytes=lambda url: sums if url.endswith("SHA256SUMS") else raw, run_cmd=run)
            argv, kwargs = calls[-1]
            for flag, expected in zip(flags, ("Core", "legacy", "por,fra", config["data_root"])):
                self.assertEqual(argv[argv.index(flag)+1], expected)
            self.assertIn(flags[-1], argv)
            self.assertEqual(kwargs["env"]["TRASLADO_SHORTCUT_DESKTOP_DIR"], str(self.root / "desktop"))
            self.assertNotIn("TRASLADO_SHORTCUT_ICONS_DIR", kwargs["env"])
            self.assertIn("refs/tags/v2.0.1", calls[0][0])
        # The same saved configuration also preserves enabled shortcuts.
        config["shortcuts"]["enabled"] = True
        plat._atomic(self.root / "current.json", config)
        with patch.object(revocation, "verify_promotion", return_value={"status": "ok"}):
            lifecycle.update_release(root=self.root, platform_name="linux", gh_cmd=False,
                fetch_json=lambda url: {"tag_name": "v2.0.1"}, fetch_bytes=lambda url: sums if url.endswith("SHA256SUMS") else raw, run_cmd=run)
        self.assertNotIn("--no-shortcut", calls[-1][0])

    def test_n4_tessdata_larger_than_four_mib_and_absolute_cap(self):
        from unittest.mock import MagicMock
        content = b"x" * (5 * 1024 * 1024)
        license_bytes = b"license"
        lock = Path(self.tmp.name) / "lock.json"
        assets = [{"name": n, "url": "https://example.invalid/"+n, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                  for n, data in (("por.traineddata", content), ("TESSDATA-LICENSE.txt", license_bytes))]
        lock.write_text(json.dumps({"assets": assets}))
        seen = []
        def open_url(req, timeout):
            data = content if req.full_url.endswith("por.traineddata") else license_bytes
            stream = MagicMock()
            def read(n):
                seen.append(n)
                return data[:n]
            stream.__enter__.return_value.read.side_effect = read
            return stream
        with patch.object(revocation.urllib.request, "urlopen", side_effect=open_url):
            result = lifecycle._install_tessdata_from_lock(self.root / "ocr", ["por"], lock)
        self.assertEqual((result / "por.traineddata").read_bytes(), content)
        self.assertEqual(seen[0], (len(content)*101+99)//100 + 1)
        assets[0]["bytes"] = 64*1024*1024 + 1
        lock.write_text(json.dumps({"assets": assets}))
        with self.assertRaisesRegex(ValueError, "limite"), patch.object(revocation.urllib.request, "urlopen") as fetch:
            lifecycle._install_tessdata_from_lock(self.root / "ocr2", ["por"], lock)
        fetch.assert_not_called()

    def test_n5_uninstall_refuses_unknown_data_and_ancestor(self):
        self.root.mkdir()
        for name in ("current.json", "candidate.json"):
            (self.root / name).write_text("invalid json")
        with self.assertRaisesRegex(ValueError, "ilegiveis"):
            lifecycle.uninstall_managed(root=self.root, yes=True)
        self.assertTrue(self.root.exists())
        plat._atomic(self.root / "current.json", {"data_root": str(self.root.parent)})
        with self.assertRaisesRegex(ValueError, "ancestral"):
            lifecycle.uninstall_managed(root=self.root, yes=True)
        self.assertTrue((self.root / "current.json").exists())

    def test_n5_windows_uninstall_deferred_until_later_execution(self):
        from traslado import cli
        current = self.setup()
        release = self.root / "releases" / current["release_id"]
        with patch.object(lifecycle, "release_in_use", return_value=True):
            result = lifecycle.uninstall_managed(root=self.root, yes=True, platform_name="win32")
        self.assertEqual(result["status"], "uninstall_pending")
        self.assertTrue(release.is_dir())
        self.assertTrue((self.root / "state/pending-uninstall.json").is_file())
        with patch.object(lifecycle, "release_in_use", return_value=False):
            self.assertEqual(cli.main(["where", "--root", str(self.root)]), 1)  # where não retoma nem remove nada
            self.assertTrue(release.exists())
            self.assertEqual(cli.main(["uninstall", "--yes", "--root", str(self.root)]), 0)
        self.assertFalse(release.exists())
        self.assertTrue(Path(current["data_root"]).exists())

    def test_n5_deferred_removal_refuses_root_outside_links_and_preserved_data(self):
        from base64 import b64decode
        root = self.root
        (root / "releases/r1/dados").mkdir(parents=True)
        (root / "releases/r2").mkdir(parents=True)
        launched = []
        with patch.object(lifecycle.subprocess, "Popen", side_effect=lambda argv, **kw: launched.append((argv, kw["env"]))):
            for alvos in ([root], [root.parent], [root / "releases"], [root / "releases/r1"]):
                with self.subTest(alvos=alvos), self.assertRaises(ValueError):
                    lifecycle._defer_self_removal(root, alvos, [root / "releases/r1/dados"])
            self.assertEqual(launched, [])
            lifecycle._defer_self_removal(root, [root / "releases/r2"], [root / "releases/r1/dados"])
        argv, env = launched[0]
        script = b64decode(argv[-1]).decode("utf-16le")
        self.assertIn("rmdir /s /q", script)
        self.assertIn("ReparsePoint", script)
        self.assertNotIn("-Recurse", script)
        self.assertEqual(env["TRASLADO_REMOVE_KEEP"], str((root / "releases/r1/dados").resolve()))

    def test_n5_new_setup_discards_pending_uninstall_without_touching_data(self):
        current = self.setup()
        release = self.root / "releases" / current["release_id"]
        with patch.object(lifecycle, "release_in_use", return_value=True):
            lifecycle.uninstall_managed(root=self.root, yes=True, platform_name="win32")
        marker = self.root / "state/pending-uninstall.json"
        self.assertTrue(marker.is_file())
        with patch.object(lifecycle, "release_in_use", return_value=False):
            lifecycle.discard_pending_uninstall(self.root)
        self.assertFalse(marker.exists())
        self.assertFalse(release.exists())
        self.assertTrue(Path(current["data_root"]).exists())

    def test_n6_legacy_untouched_on_rejected_gates_and_without_promotion(self):
        with patch.object(lifecycle, "handle_legacy_windows") as legacy:
            with self.assertRaises(ValueError):
                self.setup(fixture_fn=lambda: False)
            legacy.assert_not_called()
            self.setup(promote=False)
            legacy.assert_not_called()
            def after(*args, **kw):
                self.assertIsNotNone(plat.read_current(self.root))
            legacy.side_effect = after
            self.setup()
            legacy.assert_called_once()

    def test_n7_shortcut_failure_reports_promoted_and_retry_command(self):
        from traslado import cli
        with patch.object(plat, "refresh_shortcuts", side_effect=OSError("desktop denied")):
            with self.assertRaisesRegex(lifecycle.ShortcutError, "promovida.*shortcuts --root"):
                self.setup(no_shortcut=False)
        current = plat.read_current(self.root)
        self.assertEqual(current["release_id"], "2.0.0_a")
        with patch.object(lifecycle, "setup_release", side_effect=lifecycle.ShortcutError("promovida; atalhos")):
            self.assertEqual(cli.main(["setup"]), 4)
        with patch.object(plat, "refresh_shortcuts") as refresh:
            self.assertEqual(cli.main(["shortcuts", "--root", str(self.root)]), 0)
            refresh.assert_called_once()

    @unittest.skipUnless((ROOT / ".github/workflows/revocation.yml").is_file(), "workflows ausentes no pacote extraído (rodam no job de build)")
    def test_n8_workflow_first_issue_only_when_release_absent(self):
        workflow = (ROOT / ".github/workflows/revocation.yml").read_text()
        self.assertIn('case "$RELEASE_STATUS" in\n                404) PREV_SEQ=0', workflow)
        self.assertIn('200) echo "Release revocation existe sem revoked.json; emissao recusada" >&2; exit 1', workflow)
        self.assertIn('404) PRODUCT_LATEST="";', workflow)
        self.assertIn('if [ -n "$PRODUCT_LATEST" ]; then', workflow)
        self.assertIn('latest nao conferido', workflow)

    @unittest.skipUnless((ROOT / ".github/workflows/revocation.yml").is_file(), "workflows ausentes no pacote extraído (rodam no job de build)")
    def test_n9_sequence_content_and_gh_ref(self):
        from tests.test_lifecycle_e5 import _iso_offset
        doc = dict(schema=revocation.SCHEMA_REVOKED, sequence=1, issued_at=_iso_offset(-1), expires_at=_iso_offset(7), revoked=[])
        raw = json.dumps(doc).encode()
        release = dict(draft=False, prerelease=False, tag_name=f"v{__version__}", assets=[{"name": k, "digest": "sha256:"+v} for k,v in DIGESTS.items()])
        commands = []
        def run(argv):
            commands.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        def verify(data):
            return revocation.verify_promotion(self.root, __version__, DIGESTS, gh_cmd="gh-stub", run_cmd=run,
                fetch_bytes=lambda url: data, fetch_json=lambda url: release)
        verify(raw)
        state = (self.root / "state/revocation.json").read_bytes()
        self.assertEqual(verify(raw)["status"], "ok")
        doc["revoked"] = [{"version": "1.0.0"}]
        with self.assertRaisesRegex(ValueError, "Mesma sequence"):
            verify(json.dumps(doc).encode())
        self.assertEqual(json.loads(state)["revoked_sha256"], revocation.read_revocation_state(self.root)["revoked_sha256"])
        self.assertEqual(commands[0][-2:], ["--source-ref", "refs/heads/main"])
        cert = synthetic_certificate_bundle("https://github.com/rickfreoa/traslado/.github/workflows/revocation.yml@refs/heads/main")
        raw_cert = base64.b64decode(cert["bundle"]["verificationMaterial"]["certificate"]["rawBytes"])
        for invalid in (raw_cert[:-1], raw_cert+b"garbage", b"\x30\x80\x00\x00"):
            cert["bundle"]["verificationMaterial"]["certificate"]["rawBytes"] = base64.b64encode(invalid).decode()
            self.assertFalse(revocation._bundle_matches_identity(cert, revocation.DEFAULT_REPO))
        self.assertNotIn("_test_decode_cert", (ROOT / "src/traslado/revocation.py").read_text())
        self.assertNotIn("docx_formatting_partial", (ROOT / "src/traslado/worker.py").read_text())

    def test_n10_failed_cleanup_keep_and_prune_in_use(self):
        with self.assertRaises(ValueError):
            self.setup("bad", fixture_fn=lambda: False)
        self.assertFalse((self.root / "releases/bad").exists())
        with self.assertRaises(ValueError):
            self.setup("keep", fixture_fn=lambda: False, keep_failed=True)
        self.assertTrue((self.root / "releases/keep").exists())
        self.setup("a")
        self.setup("b")
        with patch.object(lifecycle, "release_in_use", side_effect=lambda path: path.name == "a"):
            self.setup("c")
        self.assertTrue((self.root / "releases/a").exists())
        self.assertTrue((self.root / "state/pending-removals/a.json").is_file())
        with patch.object(lifecycle, "release_in_use", return_value=False):
            lifecycle.prune_releases(self.root, pending_only=True)
        self.assertFalse((self.root / "releases/a").exists())
        self.assertTrue((self.root / "releases/b").exists())
        self.assertTrue((self.root / "releases/c").exists())

    @unittest.skipUnless(os.name == "nt", "PowerShell Windows")
    def test_n1_n9_bootstrap_remote_api_before_uv_and_install(self):
        artifacts = Path(self.tmp.name) / "assets"
        artifacts.mkdir()
        files = {WHEEL: b"wheel", "requirements-full.hashes.lock": b"full lock", "requirements-core.hashes.lock": b"core lock"}
        files["SHA256SUMS"] = "".join(hashlib.sha256(v).hexdigest()+"  "+k+"\n" for k,v in files.items()).encode()
        for name, value in files.items():
            (artifacts / name).write_bytes(value)
        api = dict(draft=False, prerelease=False, tag_name=f"v{__version__}", assets=[{"name": k, "digest": "sha256:"+hashlib.sha256(v).hexdigest()} for k,v in files.items()])
        api["assets"].append({"name": "install.ps1", "digest": "sha256:"+hashlib.sha256((ROOT / "install.ps1").read_bytes()).hexdigest()})
        metadata = Path(self.tmp.name) / "api.json"
        uvlog = Path(self.tmp.name) / "uv-called"
        uv = Path(self.tmp.name) / "uv.cmd"
        uv.write_text('@echo off\r\necho called >> "%UV_LOG%"\r\nexit /b 1\r\n')
        wrapper = Path(self.tmp.name) / "remote.ps1"
        wrapper.write_text('''function Invoke-WebRequest { param($Uri, $OutFile, [switch]$UseBasicParsing) Copy-Item -LiteralPath (Join-Path $env:ASSETS ($Uri.Split('/')[-1])) -Destination $OutFile }
function Invoke-RestMethod { param($Uri, $Headers) if ($env:API_FAIL -eq '1') { throw 'API offline' }; Get-Content -Raw $env:API_FILE | ConvertFrom-Json }
function Get-Command { [CmdletBinding()]param($Name) if ($Name -ne 'gh') { Microsoft.PowerShell.Core\\Get-Command $Name } }
& ''' + "'"+str(ROOT / "install.ps1")+"' @args\nexit $LASTEXITCODE\n")
        env = dict(os.environ, ASSETS=str(artifacts), API_FILE=str(metadata), UV_LOG=str(uvlog), TRASLADO_TEST_UV=str(uv), TRASLADO_PROMOCAO_OFFLINE="teste")
        cases = ["valid", "offline", "draft", "prerelease", "tag", *files, "install.ps1", "missing", "duplicate-sums", "version", "test-uv-outside-test"]
        for case in cases:
            with self.subTest(case=case):
                changed = json.loads(json.dumps(api))
                (artifacts / "SHA256SUMS").write_bytes(files["SHA256SUMS"])
                if uvlog.exists(): uvlog.unlink()
                if case in ("draft", "prerelease"): changed[case] = True
                if case == "tag": changed["tag_name"] = "v1.0.0"
                if case in [*files, "install.ps1"]:
                    next(a for a in changed["assets"] if a["name"] == case)["digest"] = "sha256:"+"0"*64
                if case == "missing": changed["assets"] = []
                if case == "duplicate-sums":
                    (artifacts / "SHA256SUMS").write_bytes(files["SHA256SUMS"]+files["SHA256SUMS"])
                metadata.write_text(json.dumps(changed))
                root = Path(self.tmp.name) / ("remote-"+case)
                argv = [str(lifecycle.POWERSHELL_WIN), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), "-Root", str(root), "-Profile", "Core" if case == "requirements-core.hashes.lock" else "Full"]
                if case == "version": argv += ["-Version", "../escape"]
                proc = subprocess.run(argv, env=dict(env, API_FAIL="1" if case == "offline" else "0", TRASLADO_PROMOCAO_OFFLINE="rascunho" if case == "test-uv-outside-test" else "teste"), capture_output=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0, proc.stdout+proc.stderr)
                self.assertEqual(uvlog.exists(), case == "valid", proc.stdout+proc.stderr)
                self.assertFalse((root / "current.json").exists())
        # Cross-platform bootstrap enforces the same order and input guards.
        for name in ("install.sh", "install.ps1"):
            source = (ROOT / name).read_text()
            self.assertLess(source.index("releases/tags/v"), source.index("# 1. Obter uv"))
            self.assertIn("Nome duplicado em SHA256SUMS", source)
            self.assertIn("--source-ref", source)
            self.assertIn("TRASLADO_PROMOCAO_OFFLINE", source[source.index("# 1. Obter uv"):source.index("# 4. Criar")])


    @unittest.skipUnless(shutil.which("sh") and shutil.which("awk") and (shutil.which("sha256sum") or shutil.which("shasum")), "sh/awk/sha256 ausentes")
    def test_n1_shell_api_parser_rejects_each_digest_and_release_state(self):
        """Bloco real do install.sh (sem Python no sistema): API conferida por awk antes de qualquer instalação."""
        source = (ROOT / "install.sh").read_text(encoding="utf-8")
        inicio = source.index("# Script lido de arquivo real")
        fim = source.index("# 3. Verificação de attestation")
        bloco = source[inicio:fim]
        tmp = Path(self.tmp.name)
        funcao_sha = source[source.index("sha256_file() {"):source.index("\n}\n", source.index("sha256_file() {")) + 3]
        tmp_sh = tmp.as_posix()
        # O bloco vai num arquivo (como o install.sh real), não em "sh -c": o argv do MSYS no Windows colapsa barras.
        harness = tmp / "self" / "install.sh"
        harness.parent.mkdir()
        harness.write_text(f'download_https() {{ cp "{tmp_sh}/api.json" "$2"; }}\n' + funcao_sha +
                           f'FROM_DIR=""; CLEAN_VERSION=2.0.0; STAGE_DOWNLOADS="{tmp_sh}"; DIGESTS_PATH="{tmp_sh}/digests.json"\n'
                           f'WHEEL_NAME=traslado-2.0.0-py3-none-any.whl; WHEEL_PATH="{tmp_sh}/traslado-2.0.0-py3-none-any.whl"\n'
                           f'PROFILE_LOCK_NAME=requirements-full.hashes.lock; PROFILE_LOCK_PATH="{tmp_sh}/requirements-full.hashes.lock"\n'
                           f'LEGACY_LOCK_NAME=requirements-legacy.hashes.lock; LEGACY_LOCK_PATH=""; SUMS_PATH="{tmp_sh}/SHA256SUMS"\n'
                           + bloco, encoding="utf-8", newline="\n")
        files = {}
        for name, content in (("traslado-2.0.0-py3-none-any.whl", b"wheel"), ("requirements-full.hashes.lock", b"lock"),
                              ("SHA256SUMS", b"sums")):
            (tmp / name).write_bytes(content)
            files[name] = hashlib.sha256(content).hexdigest()
        files["install.sh"] = hashlib.sha256(harness.read_bytes()).hexdigest()
        def run(api, from_pipe=False):
            (tmp / "api.json").write_text(json.dumps(api), encoding="utf-8")
            base = {k: v for k, v in files.items() if k != "install.sh"}
            (tmp / "digests.json").write_text(json.dumps(base, separators=(",", ":")) + "\n", encoding="utf-8")
            if from_pipe:  # curl ... | sh: $0 é o shell
                return subprocess.run(["sh"], input=harness.read_text(encoding="utf-8"), capture_output=True, text=True)
            return subprocess.run(["sh", harness.as_posix()], capture_output=True, text=True)
        valid = dict(draft=False, prerelease=False, tag_name="v2.0.0", name="Traslado",
                     author={"login": "x", "name": "y"},
                     assets=[{"name": k, "uploader": {"login": "u"}, "digest": "sha256:" + v} for k, v in files.items()],
                     body='"assets": [{"name": "SHA256SUMS", "digest": "sha256:00"}]')
        proc = run(valid)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads((tmp / "digests.json").read_text())["install.sh"], files["install.sh"])
        proc = run(valid, from_pipe=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("pipe", proc.stdout)
        self.assertNotIn("install.sh", json.loads((tmp / "digests.json").read_text()))
        for case in ("draft", "prerelease", "tag", "missing", "duplicate", *files):
            api = json.loads(json.dumps(valid))
            if case in ("draft", "prerelease"): api[case] = True
            if case == "tag": api["tag_name"] = "v1.0.0"
            if case == "missing": api["assets"] = []
            if case == "duplicate": api["assets"].append(dict(api["assets"][0]))
            if case in files: next(a for a in api["assets"] if a["name"] == case)["digest"] = "sha256:" + "0" * 64
            with self.subTest(case=case):
                self.assertNotEqual(run(api).returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Windows process inventory")
    def test_n5_n10_detect_running_release_process(self):
        release = self.root / "releases/running"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(release / ".venv")], check=True, capture_output=True)
        python = release / ".venv/Scripts/python.exe"
        process = subprocess.Popen([str(python), "-c", "import sys,time; print('ready', flush=True); time.sleep(30)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), b"ready")
            self.assertTrue(lifecycle.release_in_use(release))
            self.assertFalse(lifecycle.remove_unreferenced_release(self.root, release))
            self.assertTrue((self.root / "state/pending-removals/running.json").exists())
        finally:
            process.terminate()
            process.communicate(timeout=10)
        self.assertFalse(lifecycle.release_in_use(release))
        lifecycle.prune_releases(self.root, pending_only=True)
        self.assertFalse(release.exists())


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
