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
        listados = {n for n in (ROOT / "release-files.txt").read_text(encoding="utf-8").split() if n and n[0] not in "#!"}
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

    def test_membro_textual_nao_utf8_reprova_com_mensagem_propria(self):
        f = carregar("build_release").membros_proibidos
        for nome in ("script.py", "LICENCE", "notas.md", "dados.json"):
            achados = f(nome, "# Menção a MuPDF".encode("cp1252"))
            self.assertTrue(any("não UTF-8" in a for a in achados), (nome, achados))
            self.assertFalse(any(a.endswith(": conteúdo") for a in achados), achados)  # não se confunde com MuPDF binário
        self.assertEqual(f("script.py", "# Menção a MuPDF".encode("utf-8")), [])
        self.assertEqual(f("script.ps1", b"\xef\xbb\xbf# com BOM UTF-8"), [])
        self.assertEqual(f("sem_mupdf.txt", "acentuação".encode("cp1252")) != [], True)  # ainda reprova: exigência é UTF-8
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("pacote/script.py", "# Menção".encode("cp1252"))
        self.assertTrue(any("não UTF-8" in a for a in f("p.zip", buf.getvalue())))

    def test_conteudo_binario_vale_em_qualquer_extensao(self):
        f = carregar("build_release").membros_proibidos
        self.assertTrue(f("payload.txt", b"MZ\x00\x00MuPDF"))  # NUL e assinatura PE, mesmo com extensão de texto
        self.assertTrue(f("notas.json", b"\x7fELF\x02\x01 mupdf_open"))
        self.assertTrue(f("dados.md", b"\xcf\xfa\xed\xfe\x00 mupdf"))  # Mach-O
        self.assertTrue(f("x.py", b"\xff\xfe\x00 not utf8 mupdf"))  # não UTF-8
        self.assertEqual(f("x.dll", b"MZ\x00\x00 sem a palavra proibida"), [])
        self.assertEqual(f("nota.txt", "Menção legítima a MuPDF e PyMuPDF em texto UTF-8: ação".encode("utf-8")), [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a/payload.txt", b"MZ\x00\x00MuPDF")
        self.assertTrue(f("pacote.zip", buf.getvalue()))


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

    def test_versao_divergente_no_lock_com_hashes_falha(self):
        caminho = self.tmp / "requirements-full.hashes.lock"
        texto = caminho.read_text(encoding="utf-8")
        self.assertIn("lxml==6.1.3", texto)
        caminho.write_text(texto.replace("lxml==6.1.3", "lxml==6.1.2"), encoding="utf-8")
        div = self.inv.verificar(self.tmp)[0]
        # só o lock com hashes foi alterado: o código antigo (que comparava versões apenas com requirements-full.lock) não via nada
        self.assertTrue(any("lxml" in x and "hashes" in x for x in div), div)
        self.assertEqual(len(div), 2, div)  # diverge do outro lock e do inventário

    def test_versao_divergente_entre_locks_falha(self):
        caminho = self.tmp / "requirements-full.lock"
        caminho.write_text(caminho.read_text(encoding="utf-8").replace("Pillow==12.3.0", "Pillow==12.3.1"), encoding="utf-8")
        div = self.inv.verificar(self.tmp)[0]
        self.assertTrue(any("pillow" in x.lower() for x in div), div)

    def test_lacuna_nao_declarada_falha(self):
        div = self.alterar_inventario(lambda d: d.update(lacunas=[]))
        self.assertTrue(any("sem licença e sem lacuna declarada" in x for x in div))
        self.assertTrue(any("lacuna Linux" in x for x in div))


@unittest.skipUnless(GIT, "exige checkout git")
class SelecaoDoWheel(unittest.TestCase):
    def test_arquivo_rastreado_fora_da_lista_nao_entra_no_wheel_nem_no_zip(self):
        tmp = Path(tempfile.mkdtemp(prefix="t11-copia-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = tmp / "repo"
        for nome in subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, text=True,
                                   encoding="utf-8").stdout.split("\0"):
            if nome:
                (repo / nome).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / nome, repo / nome)
        (repo / "src/freoa_v6/extra_nao_listado.py").write_text("SEGREDO_FORA_DA_LISTA = 1\n", encoding="utf-8")
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        for comando in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "copia"]):
            subprocess.run([*git, *comando], check=True, capture_output=True)
        self.assertIn("src/freoa_v6/extra_nao_listado.py",
                      subprocess.run([*git, "ls-files"], capture_output=True, text=True).stdout.split())
        construir_copia = [sys.executable, "-I", "-X", "utf8", str(repo / "scripts/build_release.py"), "--dist", str(tmp / "dist")]
        # sem decisão explícita, módulo rastreado fora da lista reprova o build
        r = subprocess.run(construir_copia, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("módulo rastreado fora de release-files.txt", r.stderr)
        self.assertIn("extra_nao_listado.py", r.stderr)
        # com a exclusão declarada (revisável no arquivo), o build passa e o módulo não entra
        with (repo / "release-files.txt").open("a", encoding="utf-8", newline="\n") as f:
            f.write("!src/freoa_v6/extra_nao_listado.py\n")
        for comando in (["add", "-A"], ["commit", "-q", "-m", "exclui"]):
            subprocess.run([*git, *comando], check=True, capture_output=True)
        r = subprocess.run(construir_copia, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        for artefato in (tmp / "dist").glob("*.zip"), (tmp / "dist").glob("*.whl"):
            for caminho in artefato:
                with zipfile.ZipFile(caminho) as z:
                    self.assertFalse([n for n in z.namelist() if "extra_nao_listado" in n], caminho.name)
                    if caminho.suffix == ".zip":
                        for n in z.namelist():
                            if n.endswith(".whl"):
                                with zipfile.ZipFile(io.BytesIO(z.read(n))) as w:
                                    self.assertFalse([m for m in w.namelist() if "extra_nao_listado" in m])

    def test_todo_modulo_rastreado_esta_na_lista_ou_excluido(self):
        linhas = (ROOT / "release-files.txt").read_text(encoding="utf-8").split()
        rastreados = [n for n in subprocess.run(["git", "-C", str(ROOT), "ls-files", "src"], capture_output=True, text=True,
                                                encoding="utf-8").stdout.split() if n.endswith(".py")]
        self.assertIn("src/freoa_v6/registry.py", rastreados)
        self.assertIn("src/freoa_v6/registry.py", linhas)
        for n in rastreados:
            self.assertTrue(n in linhas or "!" + n in linhas, n)

    def test_conferencia_de_membros_do_wheel_rejeita_extra(self):
        b = carregar("build_release")
        nomes = ["src/freoa_v6/__init__.py", "src/freoa_v6/cli.py", "pyproject.toml"]
        wheel = Path(tempfile.mkdtemp(prefix="t11-w-")) / "x.whl"
        self.addCleanup(shutil.rmtree, wheel.parent, True)

        def montar(*membros):
            with zipfile.ZipFile(wheel, "w") as z:
                for m in membros:
                    z.writestr(m, b"")
        base = ("freoa_v6/__init__.py", "freoa_v6/cli.py", "freoa_md_converter-1.1.0.dist-info/METADATA")
        montar(*base)
        b.conferir_wheel(wheel, nomes)  # não levanta
        montar(*base, "freoa_v6/extra_nao_listado.py")
        with self.assertRaises(SystemExit):
            b.conferir_wheel(wheel, nomes)
        montar(*base[:2])  # arquivo listado faltando no wheel
        montar(base[0], base[2])
        with self.assertRaises(SystemExit):
            b.conferir_wheel(wheel, nomes)


class VersaoNoDocumento(unittest.TestCase):
    def test_versao_do_pacote_manifest_e_document_json_coincidem(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_acceptance
        import freoa_v6
        if os.environ.get("T11_EXIGE_INSTALADO"):
            from importlib.metadata import version
            self.assertEqual(version("freoa-md-converter"), freoa_v6.__version__)  # metadados do wheel instalado
        caso = test_acceptance.Acceptance("test_plain_text_sentinel_and_source_immutability")
        caso.setUp()
        try:
            raiz, documento, _ = caso.artifact(caso.job(caso.text("v.txt", "texto")))
            manifest = json.loads((raiz / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({manifest["converter"], documento["converter_version"]}, {freoa_v6.__version__})
        finally:
            caso.tearDown()


@unittest.skipUnless(GIT, "exige checkout git")
class OrigemNoModoInstalado(unittest.TestCase):
    @unittest.skipIf(os.environ.get("T11_EXIGE_INSTALADO"), "meta-teste do ambiente de desenvolvimento")
    def test_teste_do_instalador_nao_injeta_src_no_modo_instalado(self):
        """Com T11_EXIGE_INSTALADO e freoa_v6 só disponível pelo código-fonte, o teste dos subprocessos tem de falhar."""
        env = {**os.environ, "T11_EXIGE_INSTALADO": "1", "PYTHONPATH": str(ROOT / "src")}
        nome = "tests.test_installer_rc3.%s.test_cli_doctor_and_error_json_stay_utf8_when_parent_pipe_is_cp1252"
        classe = re.search(r"^class (\w+)\(.*\):(?:(?!^class ).)*?def test_cli_doctor_and_error_json_stay_utf8",
                           (ROOT / "tests/test_installer_rc3.py").read_text(encoding="utf-8"), re.M | re.S)[1]
        executar = lambda ambiente: subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", nome % classe], cwd=ROOT,  # noqa: E731
                                                   env=ambiente, capture_output=True, text=True, encoding="utf-8")
        # controle: sem T11_EXIGE_INSTALADO o mesmo teste passa (injeta src), então o cenário está saudável
        controle = executar({k: v for k, v in env.items() if k != "T11_EXIGE_INSTALADO"})
        self.assertEqual(controle.returncode, 0, controle.stderr[-2000:])
        r = executar(env)
        saida = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, saida[-2000:])
        # a causa tem de ser a importação de freoa_v6 no subprocesso, reportada pela asserção do teste do instalador
        self.assertIn("FAIL: test_cli_doctor_and_error_json_stay_utf8_when_parent_pipe_is_cp1252", saida)
        self.assertIn("No module named 'freoa_v6'", saida.replace("\\'", "'"))


@unittest.skipUnless((ROOT / ".github/workflows").is_dir(), "workflows ausentes no pacote extraído")
class Workflows(unittest.TestCase):
    def texto(self, nome):
        return (ROOT / ".github/workflows" / nome).read_text(encoding="utf-8")

    def test_todas_as_actions_fixadas_por_sha_completo(self):
        usos = [l for n in ("ci.yml", "release.yml") for l in self.texto(n).splitlines() if re.match(r"\s*(- )?uses:", l)]
        self.assertGreaterEqual(len(usos), 8)
        for linha in usos:
            self.assertRegex(linha, r"uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*v\d", linha)

    def test_release_exige_ci_verde_do_mesmo_commit_antes_do_rascunho(self):
        t = self.texto("release.yml")
        gate = t.index("actions/workflows/ci.yml/runs?head_sha=$GITHUB_SHA")
        self.assertLess(gate, t.index("gh release create"))
        passo = t[gate - 600:t.index("attest-build-provenance")]
        # espera a matriz (60 min, passo de 30 s), decide pela execução mais recente e recusa falha ou prazo esgotado
        for trecho in ("timeout-minutes: 65", "PRAZO=$((SECONDS + 3600))", "sleep 30", "gh api --paginate",
                       "python scripts/ci_gate.py", "liberar) break", "recusar)", "exit 1", "per_page=100", "|| DECISAO=erro-na-consulta"):
            self.assertIn(trecho, passo)
        self.assertNotIn("--jq", passo)  # a decisão não fica em jq
        self.assertNotIn("status=success", passo)  # sucesso antigo não pode mascarar execução mais recente
        self.assertLess(gate, t.index("attest-build-provenance"))
        self.assertIn("actions: read", t)
        self.assertIn("--draft", t)


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


def extra_ausente(exc: BaseException | None, opcionais) -> bool:
    """Skip só para o tipo exato ModuleNotFoundError cuja raiz é um extra opcional realmente ausente neste ambiente.

    Submódulo inexistente de pacote presente (ou instalação quebrada) é falha; texto de mensagem nunca vale."""
    if type(exc) is not ModuleNotFoundError:
        return False
    raiz = (exc.name or "").split(".")[0]
    return raiz in opcionais and importlib.util.find_spec(raiz) is None


def falha_de_importacao(nome: str, exc: BaseException) -> unittest.TestCase:
    class Falha(unittest.TestCase):
        def runTest(self):
            raise exc
    Falha.__name__ = f"ImportacaoDe_{nome}"
    return Falha()


def declaracoes_de_skip(arquivo: Path) -> set[tuple[str, str]]:
    """Linhas '<modulo_de_teste> <raiz_opcional>  # motivo'; arquivo ausente = nada declarado."""
    if not arquivo.is_file():
        return set()
    pares = set()
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        campos = linha.split("#", 1)[0].split()
        if campos:
            assert len(campos) == 2, f"{arquivo.name}: linha inválida: {linha!r}"
            pares.add((campos[0], campos[1]))
    return pares


def executar_core(pasta: Path | None = None, opcionais=OPCIONAIS, fluxo=None) -> int:
    """Roda a suíte no perfil Core: ausência de módulo de extra opcional vira skip; qualquer outra falha continua falha.

    Cada módulo de teste é importado aqui, com o tipo da exceção à vista (o loader do unittest só guarda o texto).
    """
    pasta = pasta or Path(__file__).resolve().parent
    declarados, usados = declaracoes_de_skip(pasta / "core-skips.txt"), set()

    def autorizado(modulo: str, exc: BaseException) -> bool:
        """Skip exige extra realmente ausente E o par (módulo de teste, raiz) declarado em core-skips.txt."""
        if not extra_ausente(exc, opcionais):
            return False
        par = (modulo, exc.name.split(".")[0])
        usados.add(par)
        return par in declarados

    class Resultado(unittest.TextTestResult):
        def addError(self, test, err):
            if autorizado(type(test).__module__, err[1]):
                return self.addSkip(test, f"Core: extra ausente ({err[1].name})")
            super().addError(test, err)

    sys.path.insert(0, str(pasta))
    loader, suite, pulados = unittest.TestLoader(), unittest.TestSuite(), []
    try:
        for arquivo in sorted(pasta.glob("test_*.py")):
            try:
                modulo = importlib.import_module(arquivo.stem)
            except Exception as exc:
                if autorizado(arquivo.stem, exc):
                    pulados.append(f"{arquivo.stem} ({exc.name})")
                else:  # vira falha visível, com a exceção original
                    suite.addTest(falha_de_importacao(arquivo.stem, exc))
                continue
            suite.addTests(loader.loadTestsFromModule(modulo))
        resultado = unittest.TextTestRunner(stream=fluxo, verbosity=1, resultclass=Resultado).run(suite)
    finally:
        sys.path.remove(str(pasta))
    print("Core: módulos de teste pulados por extra ausente:", ", ".join(pulados) or "nenhum", file=fluxo or sys.stderr)
    for modulo, raiz in sorted(declarados - usados):  # aviso, não falha: a declaração pode ter deixado de ser necessária
        print(f"AVISO core-skips.txt: '{modulo} {raiz}' não foi usado neste Core", file=fluxo or sys.stderr)
    return 0 if resultado.wasSuccessful() else 1


class RunnerCore(unittest.TestCase):
    """O modo --core não pode transformar erro real em skip."""

    def rodar(self, declarar=None, **arquivos):
        pasta = Path(tempfile.mkdtemp(prefix="t11-core-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        for nome, texto in arquivos.items():
            (pasta / f"{nome}.py").write_text(texto, encoding="utf-8")
            self.addCleanup(sys.modules.pop, nome, None)
        if declarar is not None:
            (pasta / "core-skips.txt").write_text(declarar, encoding="utf-8")
        self.saida = io.StringIO()
        return executar_core(pasta, {"modulo_opcional_fake"}, self.saida)

    CORPO = "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        {}\n"
    DISFARCE = 'raise ValueError("ModuleNotFoundError: No module named \'modulo_opcional_fake\'")'

    DECL = "test_t11fake_a modulo_opcional_fake  # importação\ntest_t11fake_b modulo_opcional_fake\n"

    def test_extra_ausente_declarado_vira_skip(self):
        self.assertEqual(self.rodar(self.DECL, test_t11fake_a="import modulo_opcional_fake\n",
                                    test_t11fake_b=self.CORPO.format("import modulo_opcional_fake")), 0)
        self.assertNotIn("AVISO", self.saida.getvalue())

    def test_extra_ausente_sem_declaracao_falha(self):
        corpo = self.CORPO.format("import modulo_opcional_fake")
        self.assertEqual(self.rodar(test_t11fake_h=corpo), 1)  # sem core-skips.txt
        self.assertEqual(self.rodar("test_t11fake_outro modulo_opcional_fake\n", test_t11fake_i=corpo), 1)  # par de outro módulo
        self.assertEqual(self.rodar("test_t11fake_j outro_extra\n", test_t11fake_j=corpo), 1)  # raiz diferente
        self.assertEqual(self.rodar(test_t11fake_k="import modulo_opcional_fake\n"), 1)  # falha na importação

    def test_declaracao_nao_usada_apenas_avisa(self):
        self.assertEqual(self.rodar("test_t11fake_l modulo_opcional_fake\n",
                                    test_t11fake_l=self.CORPO.format("pass")), 0)
        self.assertIn("AVISO core-skips.txt: 'test_t11fake_l modulo_opcional_fake'", self.saida.getvalue())

    def test_declaracao_nao_autoriza_extra_presente(self):
        # declarar não basta: a raiz precisa estar realmente ausente (json existe)
        pasta = Path(tempfile.mkdtemp(prefix="t11-core-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        (pasta / "test_t11fake_m.py").write_text(self.CORPO.format("import json.interno_inexistente"), encoding="utf-8")
        (pasta / "core-skips.txt").write_text("test_t11fake_m json\n", encoding="utf-8")
        self.addCleanup(sys.modules.pop, "test_t11fake_m", None)
        self.assertEqual(executar_core(pasta, {"json"}, io.StringIO()), 1)

    def test_valueerror_com_mensagem_de_modulo_ausente_falha_no_corpo(self):
        self.assertEqual(self.rodar(test_t11fake_c=self.CORPO.format(self.DISFARCE)), 1)

    def test_valueerror_com_mensagem_de_modulo_ausente_falha_na_importacao(self):
        self.assertEqual(self.rodar(test_t11fake_d=self.DISFARCE + "\n"), 1)

    def test_submodulo_inexistente_de_pacote_presente_falha_no_runner(self):
        corpo = self.CORPO.format("import json.interno_inexistente")
        pasta = Path(tempfile.mkdtemp(prefix="t11-core-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        (pasta / "test_t11fake_g.py").write_text(corpo, encoding="utf-8")
        self.addCleanup(sys.modules.pop, "test_t11fake_g", None)
        self.assertEqual(executar_core(pasta, {"json"}, io.StringIO()), 1)

    def test_modulo_ausente_que_nao_e_extra_falha(self):
        self.assertEqual(self.rodar(test_t11fake_e="import modulo_que_nao_e_extra_xyz\n"), 1)
        self.assertEqual(self.rodar(test_t11fake_f=self.CORPO.format("import modulo_que_nao_e_extra_xyz")), 1)

    def test_classificacao_exige_tipo_exato(self):
        fake = {"modulo_opcional_fake"}
        self.assertTrue(extra_ausente(ModuleNotFoundError("x", name="modulo_opcional_fake"), fake))
        self.assertTrue(extra_ausente(ModuleNotFoundError("x", name="modulo_opcional_fake.sub"), fake))  # raiz ausente de verdade
        # raiz presente: submódulo inexistente é falha, não skip
        self.assertFalse(extra_ausente(ModuleNotFoundError("x", name="json.interno_inexistente"), {"json"}))
        if importlib.util.find_spec("PIL"):
            self.assertFalse(extra_ausente(ModuleNotFoundError("x", name="PIL.interno_inexistente"), OPCIONAIS))
        self.assertFalse(extra_ausente(ModuleNotFoundError("x", name="freoa_v6.cli"), OPCIONAIS))
        self.assertFalse(extra_ausente(ImportError("x", name="fitz"), OPCIONAIS))
        self.assertFalse(extra_ausente(ValueError("No module named 'fitz'"), OPCIONAIS))


class CiGate(unittest.TestCase):
    """scripts/ci_gate.py só decide; o laço e o prazo ficam no release.yml."""

    @staticmethod
    def execucao(id, status="completed", conclusion="success", iniciado="2026-10-26T10:00:00Z", criado=None):
        return {"id": id, "status": status, "conclusion": conclusion, "run_started_at": iniciado,
                "created_at": criado or iniciado}

    def setUp(self):
        self.gate = carregar("ci_gate")

    def test_sem_execucao_aguarda(self):
        self.assertEqual(self.gate.decidir([]), "aguardar")

    def test_dados_incompletos_nunca_liberam(self):
        bom = self.execucao(1)
        for ruim in ({**self.execucao(2), "status": None}, {**self.execucao(2), "status": "estranho"},
                     {k: v for k, v in self.execucao(2, conclusion="failure").items() if k != "status"},
                     {**self.execucao(2, conclusion="failure"), "run_started_at": None, "created_at": None},
                     {**self.execucao(2), "id": None}):
            self.assertEqual(self.gate.decidir([bom, ruim]), "recusar", ruim)

    def test_sucesso_libera_e_falha_recusa(self):
        self.assertEqual(self.gate.decidir([self.execucao(1)]), "liberar")
        self.assertEqual(self.gate.decidir([self.execucao(1, conclusion="failure")]), "recusar")
        self.assertEqual(self.gate.decidir([self.execucao(1, conclusion="cancelled")]), "recusar")

    def test_execucao_em_andamento_aguarda_mesmo_com_sucesso_antigo(self):
        antigo = self.execucao(1)
        for status in ("queued", "in_progress", "waiting"):
            novo = self.execucao(2, status=status, conclusion=None, iniciado="2026-10-26T11:00:00Z")
            self.assertEqual(self.gate.decidir([antigo, novo]), "aguardar", status)

    def test_empate_de_data_desempata_pelo_id(self):
        a = self.execucao(10, conclusion="failure")
        b = self.execucao(11, conclusion="success")
        self.assertEqual(self.gate.decidir([a, b]), "liberar")
        self.assertEqual(self.gate.decidir([b, a]), "liberar")
        c = self.execucao(12, conclusion="failure")
        self.assertEqual(self.gate.decidir([a, b, c]), "recusar")

    def test_reexecucao_falha_de_run_antigo_depois_de_run_novo_verde_recusa(self):
        antigo_reexecutado = self.execucao(1, conclusion="failure", criado="2026-10-26T09:00:00Z", iniciado="2026-10-26T12:00:00Z")
        novo_verde = self.execucao(2, conclusion="success", iniciado="2026-10-26T10:00:00Z")
        self.assertEqual(self.gate.decidir([novo_verde, antigo_reexecutado]), "recusar")
        # ordenar por created_at liberaria erradamente: o run de id 2 é o mais recente por criação
        self.assertGreater(novo_verde["created_at"], antigo_reexecutado["created_at"])

    def test_leitura_de_todas_as_paginas_concatenadas(self):
        p1 = json.dumps({"total_count": 3, "workflow_runs": [self.execucao(1), self.execucao(2, conclusion="failure", iniciado="2026-10-26T10:30:00Z")]})
        p2 = json.dumps({"total_count": 3, "workflow_runs": [self.execucao(3, iniciado="2026-10-26T10:45:00Z")]})
        self.assertEqual(self.gate.decidir(self.gate.execucoes(p1 + "\n" + p2)), "liberar")
        self.assertEqual(self.gate.decidir(self.gate.execucoes(p1)), "recusar")
        self.assertEqual(self.gate.execucoes(""), [])

    def test_cli_imprime_a_decisao(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPTS / "ci_gate.py")], input=json.dumps({"workflow_runs": [self.execucao(1)]}),
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "liberar")


class Nucleo(unittest.TestCase):
    MODULOS = ["cli", "runner", "formats", "registry", "doctor", "model", "render", "chunks", "safety", "worker", "runtime"]

    def test_nucleo_importa_sem_extras(self):
        """Processo limpo com os extras bloqueados: import pesado indevido no núcleo não pode virar skip no Core."""
        codigo = (
            "import importlib, importlib.util, os, sys\n"
            "if not os.environ.get('T11_EXIGE_INSTALADO'): sys.path.insert(0, %r)\n"
            "class Bloqueio:\n"
            "    def find_spec(self, nome, caminho=None, alvo=None):\n"
            "        if nome.split('.')[0] in %r: raise ModuleNotFoundError(nome, name=nome)\n"
            "sys.meta_path.insert(0, Bloqueio())\n"
            "for m in %r:\n"
            "    importlib.import_module('freoa_v6.' + m)\n"  # lista declarada: módulo ausente é falha, nunca skip
        ) % (str(ROOT / "src"), sorted(OPCIONAIS), self.MODULOS)
        r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", codigo], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])


if __name__ == "__main__":
    sys.exit(executar_core()) if "--core" in sys.argv else unittest.main()
