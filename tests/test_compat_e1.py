# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Contrato E1: imports sem cópia, execução isolada e entry points do wheel real."""
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class CompatE1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="traslado-e1-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        target = cls.root / "venv"
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(target)
        cls.python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = target / ("Lib/site-packages" if os.name == "nt" else
                         f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        (site / "e1-source.pth").write_text(str(ROOT / "src") + "\n", encoding="utf-8")
        # -I ignores PYTHONSTARTUP; this site hook exercises prior package
        # imports in each order without preloading the executable submodule.
        (site / "e1_prior_imports.py").write_text(
            "import importlib, os\n"
            "for name in filter(None, os.environ.get('E1_PRIOR_IMPORTS', '').split(',')):\n"
            "    importlib.import_module(name)\n", encoding="utf-8")
        # Process source.pth first: site reports hook import errors on stderr
        # and continues startup, which would otherwise leave this case untested.
        (site / "e1-startup.pth").write_text("import e1_prior_imports\n", encoding="utf-8")
        # Full dependencies may live in a separate test environment. -I still
        # loads site directories, while ignoring PYTHONPATH and the checkout.
        paths = [p for p in sys.path if p.endswith("site-packages")]
        (site / "test-dependencies.pth").write_text("\n".join(paths), encoding="utf-8")
        # document.json includes elapsed extraction time in 1.1.0. Freeze only
        # worker.main's clock, before its first instruction; real timeout clocks
        # in the runner/safety modules stay intact. No JSON field is normalized.
        (site / "e1_clock.py").write_text(
            "import sys, types\n"
            "def freeze(frame, event, arg):\n"
            "    if event == 'call' and frame.f_code.co_name == 'main' and "
            "frame.f_code.co_filename.replace('\\\\', '/').endswith('/traslado/worker.py'):\n"
            "        frame.f_globals['time'] = types.SimpleNamespace(monotonic=lambda: 0.0)\n"
            "        sys.setprofile(None)\n"
            "sys.setprofile(freeze)\n", encoding="utf-8")
        (site / "e1-clock.pth").write_text("import e1_clock\n", encoding="utf-8")


    def run_module_bytes(self, module, *args, env=None):
        return subprocess.run([str(self.python), "-I", "-m", module, *map(str, args)],
                              capture_output=True, env=env, timeout=120)

    def run_module(self, module, *args):
        result = self.run_module_bytes(module, *args)
        result.stdout = result.stdout.decode("utf-8", errors="strict")
        result.stderr = result.stderr.decode("utf-8", errors="strict")
        return result

    def test_all_module_objects(self):
        self.check_import_order("traslado", "freoa_v6")

    def test_reverse_import_order(self):
        self.check_import_order("freoa_v6", "traslado")

    def check_import_order(self, first, second):
        # Discover names without importing either package, then give every
        # module/order pair its own process so transitive imports cannot mask it.
        suffixes = [""] + ["." + p.stem for p in sorted((ROOT / "src/traslado").glob("*.py"))
                           if p.stem != "__init__"]
        for suffix in suffixes:
            with self.subTest(first=first, module=suffix):
                code = (
                    "import importlib, sys\n"
                    "assert 'traslado' not in sys.modules\n"
                    "assert 'freoa_v6' not in sys.modules\n"
                    f"a = importlib.import_module({first + suffix!r})\n"
                    "assert 'traslado' in sys.modules\n"
                    f"b = importlib.import_module({second + suffix!r})\n"
                    "assert a is b\n"
                    "assert sys.modules['freoa_v6'] is sys.modules['traslado']\n"
                    f"assert a.__spec__.name == {'traslado' + suffix!r}\n"
                )
                result = subprocess.run([str(self.python), "-I", "-c", code],
                                        capture_output=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout + result.stderr, b"")

    def test_fresh_module_with_prior_package_imports(self):
        request = self.root / "requisição-ausente.json"
        for order in ("freoa_v6,traslado", "traslado,freoa_v6"):
            env = dict(os.environ, E1_PRIOR_IMPORTS=order,
                       PYTHONSTARTUP=str(self.root / "startup-must-not-run.py"))
            Path(env["PYTHONSTARTUP"]).write_text("raise RuntimeError('PYTHONSTARTUP ran')\n", encoding="utf-8")
            for module in ("freoa_v6", "freoa_v6.cli", "freoa_v6.worker"):
                with self.subTest(order=order, module=module):
                    args = (request,) if module.endswith("worker") else ("--version",)
                    result = self.run_module_bytes(module, *args, env=env)
                    canonical = self.run_module_bytes(module.replace("freoa_v6", "traslado", 1), *args, env=env)
                    stderr = result.stderr.decode("utf-8", errors="strict")
                    canonical_stderr = canonical.stderr.decode("utf-8", errors="strict")
                    self.assertEqual(result.returncode, canonical.returncode, stderr)
                    if module.endswith("worker"):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(request.name, stderr)
                        self.assertEqual(stderr.splitlines()[-1], canonical_stderr.splitlines()[-1])
                    else:
                        self.assertEqual(result.returncode, 0, stderr)
                        self.assertEqual(result.stdout.strip(), b"2.0.0")
                        self.assertEqual(result.stderr.split(b"\n", 1)[1], canonical.stderr)
                    self.assertEqual(result.stdout, canonical.stdout)
                    self.assertNotIn(b"Deprecia", result.stdout)
                    self.assertEqual(stderr.splitlines().count(
                        "Depreciação: use python -m traslado em vez de freoa_v6."), 1)

    def test_runpy_origins(self):
        result = subprocess.run([str(self.python), "-I", "-c",
            "import importlib.util,freoa_v6; "
            "names=('__main__','cli','worker','gui'); "
            "assert all(importlib.util.find_spec('freoa_v6.'+n).origin == "
            "importlib.util.find_spec('traslado.'+n).origin is not None for n in names)"],
            capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")

    def test_version(self):
        for module in ("freoa_v6", "freoa_v6.cli", "traslado"):
            with self.subTest(module=module):
                result = self.run_module(module, "--version")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "2.0.0")
                self.assertEqual(len(result.stderr.splitlines()), int(module.startswith("freoa_v6")))
                if module.startswith("freoa_v6"):
                    self.assertIn("deprecia", result.stderr.lower())

    def test_worker_runpy(self):
        request = self.root / "requisição-ausente.json"
        old = self.run_module("freoa_v6.worker", request)
        new = self.run_module("traslado.worker", request)
        self.assertEqual(old.returncode, new.returncode)
        self.assertNotEqual(old.returncode, 0)
        self.assertIn("Deprecia", old.stderr)
        self.assertNotIn("Deprecia", old.stdout)
        self.assertIn("requisição-ausente.json", new.stderr)

    def test_warning_and_cli_error_utf8(self):
        result = self.run_module("freoa_v6", "convert", self.root, "-o", self.root / "out",
                                 "--mode", "native", "--language", "")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        warning, error = result.stderr.splitlines()
        self.assertEqual(warning, "Depreciação: use python -m traslado em vez de freoa_v6.")
        self.assertEqual(json.loads(error)["status"], "failed")

    def test_doctor_module(self):
        for module in ("freoa_v6", "traslado"):
            result = self.run_module(module, "doctor")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["module"], "traslado")

    def test_worker_success(self):
        import hashlib
        from dataclasses import asdict
        from traslado.model import Options
        source = self.root / "worker.txt"
        source.write_text("Worker fixture.\n", encoding="utf-8")
        codes = []
        for module in ("freoa_v6.worker", "traslado.worker"):
            workspace = self.root / module
            workspace.mkdir()
            request = self.root / (module + ".json")
            request.write_text(json.dumps({"snapshot": str(source), "workspace": str(workspace),
                "source_name": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "options": asdict(Options(mode="native"))}), encoding="utf-8")
            result = self.run_module(module, request)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(len(result.stderr.splitlines()), int(module.startswith("freoa_v6")))
            codes.append((workspace / "document.md").read_bytes())
        self.assertEqual(*codes)

    def test_entry_points(self):
        import configparser
        if not (ROOT / "release-files.txt").is_file():
            self.skipTest("modo instalado: sem release-files.txt; o job de build cobre os entry points do wheel")
        # Only packaging needs the backend; conversion/import tests use src.
        backend = subprocess.run([sys.executable, "-c", "import setuptools, wheel"],
                                 capture_output=True, timeout=30)
        if backend.returncode:
            self.skipTest("build backend ausente; a CI constrói o wheel e roda este teste")
        source = self.root / "release-source"
        source.mkdir()
        for name in (ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines():
            name = name.strip()
            if not name or name.startswith(("#", "!")):
                continue
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        dist = self.root / "dist"
        result = subprocess.run([sys.executable, "-m", "pip", "wheel", str(source),
                                 "--no-deps", "--no-index", "--no-build-isolation", "--no-cache-dir",
                                 "-w", str(dist)],
                                cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(dist / "traslado-2.0.0-py3-none-any.whl") as wheel:
            parser = configparser.ConfigParser()
            parser.read_string(wheel.read("traslado-2.0.0.dist-info/entry_points.txt").decode())
        for name in ("traslado", "freoa-md"):
            self.assertEqual(parser["console_scripts"][name], "traslado.cli:main")
        for name in ("traslado-gui", "freoa-md-gui"):
            self.assertEqual(parser["gui_scripts"][name], "traslado.gui:main")

    def compare(self, suffix):
        source = self.root / ("fixture" + suffix)
        if suffix in (".txt", ".csv", ".html"):
            source.write_text({".txt": "Texto sintético para conversão.\n", ".csv": "nome,valor\nA,1\n",
                               ".html": "<html><body><h1>Título</h1><p>Texto.</p></body></html>"}[suffix], encoding="utf-8")
        elif suffix == ".pdf":
            fitz = self.optional("fitz")
            with fitz.open() as doc:
                doc.new_page().insert_text((50, 60), "Native PDF fixture.")
                doc.save(source)
        elif suffix == ".docx":
            doc = self.optional("docx").Document(); doc.add_paragraph("Texto sintético."); doc.save(source)
        elif suffix == ".xlsx":
            doc = self.optional("openpyxl").Workbook(); doc.active.append(["Nome", "Valor"])
            doc.active.append(["A", 1]); doc.save(source); doc.close()
        elif suffix == ".pptx":
            doc = self.optional("pptx").Presentation()
            slide = doc.slides.add_slide(doc.slide_layouts[1]); slide.shapes.title.text = "Texto sintético"
            doc.save(source)
        else:
            image = self.optional("PIL.Image")
            with image.new("RGB", (200, 100), "white") as img: img.save(source)
        outputs = []
        for module in ("freoa_v6", "traslado"):
            out = self.root / (suffix[1:] + module)
            result = self.run_module(module, "convert", source, "-o", out, "--mode", "native", "--chunks")
            self.assertNotIn("deprec", result.stdout.lower())
            self.assertEqual(len(result.stderr.splitlines()), int(module == "freoa_v6"), result.stderr)
            if module == "freoa_v6": self.assertIn("deprecia", result.stderr.lower())
            records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            self.assertTrue(records, result.stdout + result.stderr)
            outputs.append((result.returncode, Path(records[-1]["output"])))
        self.assertEqual(outputs[0][0], outputs[1][0])
        self.assertIn(outputs[0][0], (0, 3))
        left, right = outputs[0][1], outputs[1][1]
        self.assertEqual(sorted(p.relative_to(left).as_posix() for p in left.rglob("*") if p.is_file()),
                         sorted(p.relative_to(right).as_posix() for p in right.rglob("*") if p.is_file()))
        for member in left.rglob("*"):
            if not member.is_file(): continue
            other = right / member.relative_to(left)
            if member.relative_to(left).as_posix() in ("document.json", "manifest.json"):
                a, b = json.loads(member.read_bytes()), json.loads(other.read_bytes())
                for obj in (a, b):
                    field = "converter_version" if member.name == "document.json" else "converter"
                    self.assertEqual(obj[field], "2.0.0", member.name)
                    obj.pop(field)
                self.assertEqual(a, b, member.name)
            else:
                self.assertEqual(member.read_bytes(), other.read_bytes(), str(member.relative_to(left)))

    def optional(self, module):
        try: return importlib.import_module(module)
        except ModuleNotFoundError as error:
            if error.name == module or module.startswith(error.name + "."):
                self.skipTest("dependência opcional ausente: " + module)
            raise


def _case(suffix):
    def test(self): self.compare(suffix)
    return test


for _suffix in (".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".html", ".png"):
    setattr(CompatE1, "test_conversion_" + _suffix[1:], _case(_suffix))
