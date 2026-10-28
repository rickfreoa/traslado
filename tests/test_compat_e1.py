"""Contrato E1: wheel real, imports sem cópia e execução isolada dos dois nomes."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import pkgutil
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
        dist = cls.root / "dist"
        cls.wheel = ROOT / "dist/traslado-2.0.0-py3-none-any.whl"
        if (ROOT / ".git").exists() or not cls.wheel.is_file():
            result = subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT),
                                     "--no-deps", "--no-index", "--no-build-isolation", "-w", str(dist)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=180)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            cls.wheel = dist / "traslado-2.0.0-py3-none-any.whl"
        target = cls.root / "venv"
        venv.EnvBuilder(system_site_packages=True, with_pip=False).create(target)
        cls.python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = target / ("Lib/site-packages" if os.name == "nt" else
                         f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
        with zipfile.ZipFile(cls.wheel) as wheel:
            wheel.extractall(site)
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


    def run_module(self, module, *args):
        return subprocess.run([str(self.python), "-I", "-X", "utf8", "-m", module, *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_all_module_objects(self):
        import traslado
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertIs(importlib.import_module("freoa_v6"), traslado)
            for module in pkgutil.walk_packages(traslado.__path__, "traslado."):
                with self.subTest(module=module.name):
                    canonical = importlib.import_module(module.name)
                    alias = importlib.import_module(module.name.replace("traslado", "freoa_v6", 1))
                    self.assertIs(alias, canonical)
                    self.assertEqual(canonical.__spec__.name, module.name)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_reverse_import_order(self):
        result = subprocess.run([str(self.python), "-I", "-c",
            "import importlib,pkgutil,traslado; "
            "names=[m.name for m in pkgutil.walk_packages(traslado.__path__,'traslado.')]; "
            "assert all(importlib.import_module(n.replace('traslado','freoa_v6',1)) "
            "is importlib.import_module(n) for n in names)"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")

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
        request = self.root / "missing-request.json"
        old = self.run_module("freoa_v6.worker", request)
        new = self.run_module("traslado.worker", request)
        self.assertEqual(old.returncode, new.returncode)
        self.assertNotEqual(old.returncode, 0)
        self.assertIn("Deprecia", old.stderr)
        self.assertNotIn("Deprecia", old.stdout)

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
        with zipfile.ZipFile(self.wheel) as wheel:
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
            if member.name in ("document.json", "manifest.json"):
                a, b = json.loads(member.read_bytes()), json.loads(other.read_bytes())
                for obj in (a, b):
                    obj.pop("converter_version", None); obj.pop("converter", None)
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
