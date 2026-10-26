"""Etapa 1 do Traslado 1.1: versão única, seleção explícita, build limpo e determinístico, ausência de PyMuPDF/MuPDF e A8.

Os testes de build exigem um checkout git (pulam no pacote extraído). Em CI a suíte roda contra o wheel instalado e
T11_EXIGE_INSTALADO=1 faz `test_importa_do_venv` conferir que freoa_v6 não vem de um checkout.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GIT = (ROOT / ".git").exists()


def carregar(nome: str):
    spec = importlib.util.spec_from_file_location(nome, SCRIPTS / f"{nome}.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def versao_fonte() -> str:
    return re.search(r'^__version__ = "([^"]+)"', (ROOT / "src/freoa_v6/__init__.py").read_text(encoding="utf-8"), re.M)[1]


def sujo() -> bool:
    return bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout.strip())


def construir(destino: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "build_release.py"), "--dist", str(destino), *extra],
                          capture_output=True, text=True, encoding="utf-8")


class VersaoUnica(unittest.TestCase):
    def test_fontes_coincidem(self):
        versao = versao_fonte()
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("version", pyproject["project"]["dynamic"])
        self.assertNotIn("version", pyproject["project"])
        self.assertEqual(pyproject["tool"]["setuptools"]["dynamic"]["version"], {"attr": "freoa_v6.__version__"})
        spec = importlib.util.spec_from_file_location("install_windows_t11", SCRIPTS / "install_windows.py")
        instalador = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(instalador)
        self.assertEqual(instalador.VERSION, versao)

    def test_importa_do_venv(self):
        import freoa_v6
        if os.environ.get("T11_EXIGE_INSTALADO"):
            self.assertTrue(Path(freoa_v6.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), freoa_v6.__file__)
            self.assertEqual(freoa_v6.__version__, versao_fonte())


@unittest.skipUnless(GIT, "exige checkout git")
class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="t11-build-"))
        cls.flag = ["--permitir-sujo"] if sujo() else []  # em CI a árvore é limpa e o build corre sem a opção
        cls.a, cls.b = cls.tmp / "a", cls.tmp / "b"
        for destino in (cls.a, cls.b):
            r = construir(destino, *cls.flag)
            if r.returncode:
                raise AssertionError(r.stdout + r.stderr)
        cls.zip = cls.a / f"traslado-{versao_fonte()}-windows.zip"
        cls.wheel = cls.a / f"freoa_md_converter-{versao_fonte()}-py3-none-any.whl"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def membros(self):
        with zipfile.ZipFile(self.zip) as z:
            prefixo = f"traslado-{versao_fonte()}-windows/"
            self.assertTrue(all(n.startswith(prefixo) for n in z.namelist()))
            return {n[len(prefixo):] for n in z.namelist()}

    def test_versao_do_wheel(self):
        with zipfile.ZipFile(self.wheel) as z:
            metadata = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))).decode("utf-8")
        self.assertEqual(re.search(r"^Version: (\S+)\s*$", metadata, re.M)[1], versao_fonte())

    def test_zip_contem_exatamente_o_listado(self):
        listados = {n for n in (ROOT / "release-files.txt").read_text(encoding="utf-8").split() if n}
        esperado = listados | {"INSTALL-PAYLOAD.json", f"dist/{self.wheel.name}"}
        self.assertEqual(self.membros(), esperado)
        self.assertFalse(any(n.startswith(("tests/", ".github/")) or n == "release-files.txt" for n in esperado))
        with zipfile.ZipFile(self.zip) as z:
            payload = json.loads(z.read(f"traslado-{versao_fonte()}-windows/INSTALL-PAYLOAD.json"))
        self.assertEqual(payload["checkout_limpo"], not self.flag)
        self.assertEqual({f["path"] for f in payload["files"]}, esperado - {"INSTALL-PAYLOAD.json"})

    def test_arquivo_nao_listado_na_raiz_nao_entra(self):
        extra = ROOT / "ARQUIVO-NAO-LISTADO-T11.txt"
        extra.write_text("fora da lista", encoding="utf-8")
        try:
            r = construir(self.tmp / "extra", "--permitir-sujo")
            self.assertEqual(r.returncode, 0, r.stderr)
            with zipfile.ZipFile(self.tmp / "extra" / self.zip.name) as z:
                self.assertFalse([n for n in z.namelist() if "NAO-LISTADO" in n])
                payload = json.loads(z.read(f"traslado-{versao_fonte()}-windows/INSTALL-PAYLOAD.json"))
            self.assertFalse(payload["checkout_limpo"])
        finally:
            extra.unlink()

    def test_arvore_suja_recusa(self):
        extra = ROOT / "SUJEIRA-T11.txt"
        extra.write_text("arquivo não rastreado", encoding="utf-8")
        try:
            r = construir(self.tmp / "suja")
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse((self.tmp / "suja" / self.zip.name).exists())
        finally:
            extra.unlink()

    def test_zip_deterministico(self):
        self.assertEqual((self.a / "SHA256SUMS").read_bytes(), (self.b / "SHA256SUMS").read_bytes())
        self.assertEqual(self.zip.read_bytes(), (self.b / self.zip.name).read_bytes())
        linhas = (self.a / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(l.split("  ")[1] for l in linhas), sorted([self.zip.name, self.wheel.name]))
        with zipfile.ZipFile(self.zip) as z:
            self.assertTrue(all(i.date_time == (1980, 1, 1, 0, 0, 0) and i.external_attr >> 16 == 0o100644 for i in z.infolist()))
            self.assertEqual(z.namelist(), sorted(z.namelist()))

    def test_sem_mupdf_no_zip_e_no_wheel(self):
        construcao = carregar("build_release")
        self.assertEqual(construcao.verificar([self.zip, self.wheel]), 0)


class DeteccaoMuPDF(unittest.TestCase):
    def test_detecta_nome_e_conteudo(self):
        f = carregar("build_release").membros_proibidos
        self.assertTrue(f("fitz/__init__.py", b""))
        self.assertTrue(f("pymupdf/_mupdf.pyd", b"MZ"))
        self.assertTrue(f("PyMuPDF-1.28.2.dist-info/METADATA", b""))
        self.assertTrue(f("lib/libfoo.dll", b"MZ\x00\x00 built with MuPDF 1.2"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("pkg/_mupdf.so", b"\x7fELF")
        self.assertTrue(f("dist/x.whl", buf.getvalue()))
        self.assertEqual(f("src/freoa_v6/pdf.py", b"import fitz  # PyMuPDF opcional"), [])
        self.assertEqual(f("THIRD-PARTY.md", b"PyMuPDF AGPL"), [])


class Inventario(unittest.TestCase):
    ARQUIVOS = ["THIRD-PARTY-INVENTORY.json", "THIRD-PARTY.md", "ocr-assets.lock.json", "requirements-full.lock",
                "requirements-full.hashes.lock", "requirements-test.hashes.lock", "scripts/bootstrap_ocr.ps1"]

    def setUp(self):
        self.inv = carregar("inventario")
        self.tmp = Path(tempfile.mkdtemp(prefix="t11-inv-"))
        for nome in self.ARQUIVOS:
            (self.tmp / nome).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / nome, self.tmp / nome)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def alterar_inventario(self, funcao):
        caminho = self.tmp / "THIRD-PARTY-INVENTORY.json"
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        funcao(dados)
        caminho.write_text(json.dumps(dados), encoding="utf-8")
        return self.inv.verificar(self.tmp)[0]

    def test_arvore_atual_passa_e_declara_lacunas(self):
        divergencias, lacunas = self.inv.verificar(ROOT)
        self.assertEqual(divergencias, [])
        self.assertTrue(any(x.startswith("plataforma-ocr-so-windows-x64") for x in lacunas))
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "inventario.py"), "--raiz", str(ROOT), "--saida", str(self.tmp / "s.json")],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((self.tmp / "s.json").read_text(encoding="utf-8"))["divergencias"], [])

    def test_asset_do_lock_sem_entrada_falha(self):
        div = self.alterar_inventario(lambda d: d["ocr_downloads"].pop(3))
        self.assertTrue(any("por.traineddata" in x for x in div), div)
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "inventario.py"), "--raiz", str(self.tmp), "--saida", str(self.tmp / "s.json")],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(json.loads((self.tmp / "s.json").read_text(encoding="utf-8"))["divergencias"])

    def test_outras_divergencias_sao_detectadas(self):
        self.assertTrue(self.alterar_inventario(lambda d: d["dependencies"].pop(0)))
        self.assertTrue(self.alterar_inventario(lambda d: d["ocr_engine"]["runtime_members"].pop(0)))
        self.assertTrue(self.alterar_inventario(lambda d: d["ocr_model_license"].update(expression=None)))

    def test_lacuna_nao_declarada_falha(self):
        div = self.alterar_inventario(lambda d: d.update(lacunas=[]))
        self.assertTrue(any("sem licença e sem lacuna declarada" in x for x in div))
        self.assertTrue(any("lacuna Linux" in x for x in div))


@unittest.skipUnless(os.environ.get("T11_EXIGE_OCR"), "qualificação de OCR real só nas células Full que exigem o motor")
class QualificacaoOCR(unittest.TestCase):
    """Disponibilidade não é fidelidade: falha (não pula) se o motor ou um idioma exigido faltar e exige OCR real de uma digitalização."""

    def test_motor_real_reconhece_digitalizacao(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_acceptance
        from freoa_v6.model import Options
        from freoa_v6.ocr import find_tesseract
        exe = os.environ.get("T11_TESSERACT") or find_tesseract()
        self.assertTrue(exe and Path(exe).is_file(), "motor OCR exigido ausente")
        versao = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        idiomas = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        instalados = {l.strip() for l in (idiomas.stdout or idiomas.stderr).splitlines()[1:]}
        print(f"\nT11 OCR motor={exe}\n{versao.stdout or versao.stderr}T11 OCR idiomas={sorted(instalados)}", flush=True)
        exigidos = set(filter(None, os.environ.get("T11_LANGS", "eng").split(",")))
        self.assertLessEqual(exigidos, instalados, "idioma OCR exigido ausente")
        caso = test_acceptance.Acceptance("test_clean_scan_real_ocr_recovers_exact_numbers")
        caso.setUp()
        try:
            origem, esperado = caso.scan()
            resultado = caso.job(origem, Options(language="eng", tesseract=exe))
            _, documento, md = caso.artifact(resultado)
            self.assertFalse(any("ocr_failed" in w for p in documento["pages"] for w in p["warnings"]))
            self.assertEqual(documento["pages"][0]["route"], "ocr")
            for linha in esperado:
                self.assertIn(linha, md)
        finally:
            caso.tearDown()


OPCIONAIS = {"fitz", "pymupdf", "docx", "openpyxl", "pptx", "PIL", "lxml", "xlsxwriter", "et_xmlfile"}


def executar_core() -> int:
    """Roda a suíte no perfil Core: erro por falta de módulo de extra opcional vira skip; qualquer outra falha continua falha."""
    class Resultado(unittest.TextTestResult):
        def addError(self, test, err):
            # Falha de importação de módulo de teste chega como ImportError genérico com o traceback no texto.
            achado = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", str(err[1]))
            raiz = (getattr(err[1], "name", None) or (achado[1] if achado else "")).split(".")[0]
            if raiz in OPCIONAIS and (isinstance(err[1], ModuleNotFoundError) or achado):
                return self.addSkip(test, f"Core: extra ausente ({raiz})")
            super().addError(test, err)

    pasta = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(str(pasta), top_level_dir=str(pasta))
    return 0 if unittest.TextTestRunner(verbosity=1, resultclass=Resultado).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(executar_core()) if "--core" in sys.argv else unittest.main()
