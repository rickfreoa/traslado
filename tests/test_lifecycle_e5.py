# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Testes unitários e integrados (sem rede) da Etapa 5 (E5):

1. Bootstrap com -FromDir e TRASLADO_TEST_UV apontando para mock local em raiz temporária;
2. Reinstalação idempotente (segunda execução não altera current.json se a mesma release já está ativa);
3. Candidata não promovida se o portão doctor ou o portão fixture falhar (falha injetada);
4. Promoção atômica (candidate.json -> current.json);
5. uninstall (pede confirmação sem --yes; remove com --yes; nunca remove data_root);
6. rollback (volta para release anterior);
7. Revogação em dois canais (sequence menor recusada, documento expirado recusado, divergência bloqueia,
   primeira instalação exige documento válido, revogação por versão/sha256);
8. Instalação legada Windows (%LOCALAPPDATA%\\Freoa\\MarkdownConverter com product: freoa-md-converter):
   reconhecida, atalho antigo removido, superseded-by-traslado.json gravado, raiz antiga não deletada;
9. 7-Zip (7z.exe, 7z.dll, .bootstrap-tools, .ocr-download-cache) ausente da instalação ao final.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from traslado import __version__, cli, lifecycle, platform as plat, revocation  # noqa: E402
from traslado.revocation import RevocationError  # noqa: E402


def _iso_offset(days: int) -> str:
    ts = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _criar_venv_rapido(venv_dir: Path, instalar_traslado: bool = True) -> Path:
    """Cria um .venv local sem rede reutilizando site-packages do interpretador de teste."""
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(venv_dir)],
        check=True,
        capture_output=True,
    )
    if os.name == "nt":
        site_dir = venv_dir / "Lib" / "site-packages"
        bin_dir = venv_dir / "Scripts"
        py_exe = bin_dir / "python.exe"
    else:
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_dir = venv_dir / "lib" / py_ver / "site-packages"
        bin_dir = venv_dir / "bin"
        py_exe = bin_dir / "python"
    site_dir.mkdir(parents=True, exist_ok=True)
    if instalar_traslado:
        shutil.copytree(ROOT / "src" / "traslado", site_dir / "traslado", dirs_exist_ok=True)
        shutil.copytree(ROOT / "src" / "freoa_v6", site_dir / "freoa_v6", dirs_exist_ok=True)
        dist_info = site_dir / f"traslado-{__version__}.dist-info"
        dist_info.mkdir(parents=True, exist_ok=True)
        (dist_info / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: traslado\nVersion: {__version__}\n",
            encoding="utf-8",
        )
    if os.name == "nt":
        wrapper = bin_dir / "traslado.exe"
        shutil.copyfile(py_exe, wrapper)
    else:
        wrapper = bin_dir / "traslado"
        wrapper.write_text(f"#!/bin/sh\nexec \"{py_exe}\" -m traslado \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
    return py_exe


def synthetic_certificate_bundle(uri: str) -> dict:
    """Parseable synthetic X.509 certificate; no signature/trust assertion."""
    import base64
    def der(tag, value):
        n = len(value)
        length = bytes([n]) if n < 128 else bytes([128 + (n.bit_length()+7)//8]) + n.to_bytes((n.bit_length()+7)//8, 'big')
        return bytes([tag]) + length + value
    seq = lambda *values: der(0x30, b''.join(values))
    integer = lambda value: der(2, value)
    alg = bytes.fromhex('300d06092a864886f70d01010b0500')
    name = seq(der(0x31, seq(bytes.fromhex('0603550403'), der(12, b'synthetic'))))
    validity = seq(der(23, b'260101000000Z'), der(23, b'491231235959Z'))
    rsa = seq(integer(b'\x00\xb1' + b'\x11'*255), integer(b'\x01\x00\x01'))
    public = seq(bytes.fromhex('300d06092a864886f70d0101010500'), der(3, b'\x00'+rsa))
    san = seq(bytes.fromhex('0603551d11'), der(4, seq(der(0x86, uri.encode('ascii')))))
    tbs = seq(der(0xa0, integer(b'\x02')), integer(b'\x01'), alg, name, validity, name, public, der(0xa3, seq(san)))
    cert = seq(tbs, alg, der(3, b'\x00'+b'\x11'*256))
    return {"bundle": {"verificationMaterial": {"certificate": {"rawBytes": base64.b64encode(cert).decode()}}}}


class RevocationTests(unittest.TestCase):
    """Testes do item 7: protocolo de revogação em dois canais, sequence monotônica e validade."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="e5-rev-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _verify(self, root, version, expected_digests=None, **kwargs):
        if expected_digests is None:
            expected_digests = {"traslado-2.0.0-py3-none-any.whl": "a" * 64}
        return revocation.verify_promotion(root, version, expected_digests, **kwargs)

    def _doc(self, sequence: int = 5, days_valid: int = 14, revoked: list[dict] | None = None) -> dict:
        return {
            "schema": revocation.SCHEMA_REVOKED,
            "sequence": sequence,
            "issued_at": _iso_offset(min(-1, days_valid - 14)),
            "expires_at": _iso_offset(days_valid),
            "revoked": revoked or [],
        }

    def _release_info(
        self,
        tag: str = "v2.0.0",
        draft: bool = False,
        prerelease: bool = False,
        assets: list[dict] | None = None,
    ) -> dict:
        return {
            "tag_name": tag,
            "draft": draft,
            "prerelease": prerelease,
            "assets": assets
            or [
                {"name": "traslado-2.0.0-py3-none-any.whl", "digest": "sha256:" + "a" * 64},
            ],
        }

    def _mock_fetchers(self, doc: dict, release_info: dict, attestation_valid: bool = True):
        raw_doc = json.dumps(doc).encode("utf-8")

        def fetch_bytes(url: str) -> bytes:
            self.assertTrue(url.endswith("/revocation/revoked.json"), url)
            return raw_doc

        def fetch_json(url: str) -> dict:
            if "/attestations/sha256:" in url:
                if not attestation_valid:
                    return {"attestations": []}
                return {
                    "attestations": [
                        synthetic_certificate_bundle(
                            f"https://github.com/{revocation.DEFAULT_REPO}/{revocation.REVOCATION_WORKFLOW}@{revocation.REVOCATION_REF}")
                    ]
                }
            if "/releases/tags/" in url:
                return release_info
            raise AssertionError(f"URL inesperada: {url}")

        return fetch_bytes, fetch_json

    def test_primeira_instalacao_exige_documento_valido_e_persiste_estado(self) -> None:
        # Sem estado prévio e com documento expirado -> falha na primeira instalação
        fb_exp, fj_exp = self._mock_fetchers(self._doc(sequence=1, days_valid=-2), self._release_info())
        with self.assertRaises(RevocationError) as ctx:
            self._verify(
                self.tmp / "root_exp",
                "2.0.0",
                fetch_bytes=fb_exp,
                fetch_json=fj_exp,
                gh_cmd=False,
            )
        self.assertIn("vencido", str(ctx.exception))
        self.assertFalse(revocation.state_path(self.tmp / "root_exp").exists())

        # Primeira instalação com documento válido -> aceita e persiste state/revocation.json
        root_ok = self.tmp / "root_ok"
        fb_ok, fj_ok = self._mock_fetchers(self._doc(sequence=10, days_valid=14), self._release_info())
        res_ok = self._verify(
            root_ok,
            "2.0.0",
            {"traslado-2.0.0-py3-none-any.whl": "a" * 64},
            fetch_bytes=fb_ok,
            fetch_json=fj_ok,
            gh_cmd=False,
        )
        self.assertEqual(res_ok["status"], "ok")
        self.assertEqual(res_ok["mode"], "api-presence")
        estado = revocation.read_revocation_state(root_ok)
        self.assertIsNotNone(estado)
        self.assertEqual(estado["sequence"], 10)
        self.assertEqual(estado["mode"], "api-presence")

    def test_sequence_menor_recusada_contra_rollback_de_revogacao(self) -> None:
        root = self.tmp / "root"
        fb1, fj1 = self._mock_fetchers(self._doc(sequence=12, days_valid=14), self._release_info())
        self._verify(root, "2.0.0", fetch_bytes=fb1, fetch_json=fj1, gh_cmd=False)

        # Replay de documento antigo com sequence 11 (< 12) tem de ser recusado
        fb2, fj2 = self._mock_fetchers(self._doc(sequence=11, days_valid=14), self._release_info())
        with self.assertRaises(RevocationError) as ctx:
            self._verify(root, "2.0.0", fetch_bytes=fb2, fetch_json=fj2, gh_cmd=False)
        self.assertIn("Sequência regressiva", str(ctx.exception))
        estado = revocation.read_revocation_state(root)
        self.assertEqual(estado["sequence"], 12)

    def test_documento_expirado_recusado(self) -> None:
        root = self.tmp / "root"
        fb1, fj1 = self._mock_fetchers(self._doc(sequence=3, days_valid=14), self._release_info())
        self._verify(root, "2.0.0", fetch_bytes=fb1, fetch_json=fj1, gh_cmd=False)

        fb2, fj2 = self._mock_fetchers(self._doc(sequence=4, days_valid=-1), self._release_info())
        with self.assertRaises(RevocationError) as ctx:
            self._verify(root, "2.0.0", fetch_bytes=fb2, fetch_json=fj2, gh_cmd=False)
        self.assertIn("vencido", str(ctx.exception))

    def test_divergencia_entre_os_dois_canais_bloqueia(self) -> None:
        root = self.tmp / "root"
        # Caso A: Canal 1 limpo, mas Canal 2 marca release como draft
        fb_draft, fj_draft = self._mock_fetchers(self._doc(sequence=5, days_valid=14), self._release_info(draft=True))
        with self.assertRaises(RevocationError) as ctx:
            self._verify(root, "2.0.0", fetch_bytes=fb_draft, fetch_json=fj_draft, gh_cmd=False)
        self.assertIn("rascunho", str(ctx.exception))

        # Caso B: Canal 1 limpo, mas Canal 2 diverge no digest do artefato. Raiz própria: a mesma sequence gerada
        # noutro segundo tem bytes diferentes e esbarraria, com razão, na proteção de conteúdo persistido.
        fb_dig, fj_dig = self._mock_fetchers(self._doc(sequence=5, days_valid=14), self._release_info())
        with self.assertRaises(RevocationError) as ctx:
            self._verify(
                self.tmp / "root_b",
                "2.0.0",
                {"traslado-2.0.0-py3-none-any.whl": "b" * 64},
                fetch_bytes=fb_dig,
                fetch_json=fj_dig,
                gh_cmd=False,
            )
        self.assertIn("diverge do esperado", str(ctx.exception))

        # Caso C: Canal 2 limpo, mas Canal 1 lista a versão ou o SHA-256 como revogados
        fb_ver, fj_ver = self._mock_fetchers(
            self._doc(
                sequence=6,
                days_valid=14,
                revoked=[{"version": "2.0.0", "sha256": [], "reason": "CVE-teste"}],
            ),
            self._release_info(),
        )
        with self.assertRaises(RevocationError) as ctx:
            self._verify(root, "2.0.0", fetch_bytes=fb_ver, fetch_json=fj_ver, gh_cmd=False)
        self.assertIn("CVE-teste", str(ctx.exception))

        fb_sha, fj_sha = self._mock_fetchers(
            self._doc(
                sequence=7,
                days_valid=14,
                revoked=[{"version": None, "sha256": ["a" * 64], "reason": "wheel comprometido"}],
            ),
            self._release_info(),
        )
        with self.assertRaises(RevocationError) as ctx:
            self._verify(
                root,
                "2.0.0",
                {"traslado-2.0.0-py3-none-any.whl": "a" * 64},
                fetch_bytes=fb_sha,
                fetch_json=fj_sha,
                gh_cmd=False,
            )
        self.assertIn("wheel comprometido", str(ctx.exception))

    def test_modo_gh_registra_attestation_quando_gh_presente(self) -> None:
        root = self.tmp / "root"
        fb, fj = self._mock_fetchers(self._doc(sequence=7, days_valid=14), self._release_info())
        res = self._verify(
            root,
            "2.0.0",
            fetch_bytes=fb,
            fetch_json=fj,
            gh_cmd="C:/fake/gh.exe",
            run_cmd=lambda argv: subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr=""),
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["mode"], "gh")
        estado = revocation.read_revocation_state(root)
        self.assertEqual(estado["mode"], "gh")

    def test_cli_revocation_emite_documento_valido(self) -> None:
        saida = self.tmp / "revoked.json"
        rc = revocation.main([
            "--input",
            str(ROOT / "revocation" / "revoked-list.json"),
            "--output",
            str(saida),
            "--previous-sequence",
            "41",
        ])
        self.assertEqual(rc, 0)
        dados = json.loads(saida.read_text(encoding="utf-8"))
        self.assertEqual(dados["schema"], revocation.SCHEMA_REVOKED)
        self.assertEqual(dados["sequence"], 42)
        self.assertEqual(dados["revoked"], [])
        self.assertEqual(revocation.validate_revoked_document(dados)["sequence"], 42)


class LifecycleGatesAndCommandsTests(unittest.TestCase):
    """Testes dos itens 2, 3, 4, 5, 6, 8 e 9 sobre lifecycle.py e cli.py."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="e5-life-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "Traslado"
        self.data_root = self.tmp / "Documentos" / "Traslado"
        self.desktop_dir = self.tmp / "Desktop"
        self.start_menu_dir = self.tmp / "StartMenu"
        self.legacy_app = self.tmp / "Freoa" / "MarkdownConverter"
        self.desktop_dir.mkdir(parents=True, exist_ok=True)
        self.start_menu_dir.mkdir(parents=True, exist_ok=True)

        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRASLADO_SHORTCUT_DESKTOP_DIR": str(self.desktop_dir),
                "TRASLADO_SHORTCUT_START_MENU_DIR": str(self.start_menu_dir),
                "TRASLADO_LEGACY_ROOT": str(self.legacy_app),
                "TRASLADO_PROMOCAO_OFFLINE": "teste",
            },
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _preparar_release_candidata(self, release_id: str) -> Path:
        rel_dir = self.root / "releases" / release_id
        rel_dir.mkdir(parents=True, exist_ok=True)
        py_exe = plat.release_python(self.root, release_id)
        py_exe.parent.mkdir(parents=True, exist_ok=True)
        py_exe.write_bytes(b"fake-python")
        return rel_dir

    def test_promocao_atomica_e_reinstalacao_idempotente(self) -> None:
        """Itens 2 e 4: candidate.json -> current.json e segunda execução idempotente."""
        rel_id = f"{__version__}-core"
        self._preparar_release_candidata(rel_id)

        manifest1 = lifecycle.setup_release(
            root=self.root,
            release_id=rel_id,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
        )
        self.assertFalse((self.root / "candidate.json").exists())
        self.assertTrue((self.root / "current.json").is_file())
        self.assertEqual(manifest1["release_id"], rel_id)
        self.assertEqual(manifest1["gates"], {"doctor": True, "fixture": True, "revocation": "offline", "offline_reason": "teste"})
        self.assertIsNotNone(manifest1["promoted_at"])

        current_bytes_1 = (self.root / "current.json").read_bytes()

        # Reinstalação idempotente da mesma release ativa: current.json não muda byte a byte
        manifest2 = lifecycle.setup_release(
            root=self.root,
            release_id=rel_id,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
        )
        self.assertEqual(manifest2["release_id"], rel_id)
        self.assertEqual((self.root / "current.json").read_bytes(), current_bytes_1)

    def test_candidata_nao_promovida_se_portao_doctor_ou_fixture_ou_revogacao_falhar(self) -> None:
        """Item 3: falha injetada em doctor, fixture ou revogação impede a promoção de candidate.json."""
        rel_id = f"{__version__}-core"
        self._preparar_release_candidata(rel_id)

        # 3a. Portão doctor falha
        with self.assertRaises(ValueError) as ctx_doc:
            lifecycle.setup_release(
                root=self.root,
                release_id=rel_id,
                profile="Core",
                skip_ocr=True,
                no_shortcut=True,
                promote=True,
                allow_offline_promotion=True,
                data_root=self.data_root,
                doctor_fn=lambda *a, **kw: {"capabilities": "invalido"},
            )
        self.assertIn("doctor reprovado", str(ctx_doc.exception))
        self.assertFalse((self.root / "current.json").exists())
        self.assertTrue((self.root / "candidate.json").is_file())
        cand_doc = json.loads((self.root / "candidate.json").read_text(encoding="utf-8"))
        self.assertFalse(cand_doc["gates"]["doctor"])

        # 3b. Portão doctor passa, mas portão fixture falha
        with self.assertRaises(ValueError) as ctx_fix:
            lifecycle.setup_release(
                root=self.root,
                release_id=rel_id,
                profile="Core",
                skip_ocr=True,
                no_shortcut=True,
                promote=True,
                allow_offline_promotion=True,
                data_root=self.data_root,
                fixture_fn=lambda: False,
            )
        self.assertIn("fixture reprovada", str(ctx_fix.exception))
        self.assertFalse((self.root / "current.json").exists())
        cand_fix = json.loads((self.root / "candidate.json").read_text(encoding="utf-8"))
        self.assertTrue(cand_fix["gates"]["doctor"])
        self.assertFalse(cand_fix["gates"]["fixture"])

        # 3c. Doctor e fixture passam, mas revogação online bloqueia
        with self.assertRaises(ValueError) as ctx_rev:
            lifecycle.setup_release(
                root=self.root,
                release_id=rel_id,
                profile="Core",
                skip_ocr=True,
                no_shortcut=True,
                promote=True,
                allow_offline_promotion=False,
                data_root=self.data_root,
                expected_digests={f"traslado-{__version__}-py3-none-any.whl": "a"*64, "requirements-core.hashes.lock": "b"*64},
                revocation_fn=lambda *a, **kw: (_ for _ in ()).throw(RevocationError("divergência de canal")),
            )
        self.assertIn("revogação bloqueada", str(ctx_rev.exception))
        self.assertFalse((self.root / "current.json").exists())
        cand_rev = json.loads((self.root / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(cand_rev["gates"]["revocation"], "blocked")

    def test_where_rollback_e_uninstall_preservando_data_root(self) -> None:
        """Itens 5 e 6: where --json, rollback para release anterior e uninstall preservando data_root."""
        rel_a = "2.0.0-a-core"
        rel_b = "2.0.0-b-core"
        self._preparar_release_candidata(rel_a)
        self._preparar_release_candidata(rel_b)

        # Antes de qualquer instalação, `where` retorna código 1
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(cli.main(["where", "--json", "--root", str(self.root)]), 1)

        # Promove rel_a e depois rel_b
        lifecycle.setup_release(
            root=self.root,
            release_id=rel_a,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
        )
        lifecycle.setup_release(
            root=self.root,
            release_id=rel_b,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
        )
        atual = json.loads((self.root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(atual["release_id"], rel_b)
        self.assertEqual(atual["previous_release_id"], rel_a)

        # Testa `where --json` via CLI
        buf_where = io.StringIO()
        with mock.patch("sys.stdout", buf_where):
            rc_where = cli.main(["where", "--json", "--root", str(self.root)])
        self.assertEqual(rc_where, 0)
        info_where = json.loads(buf_where.getvalue())
        self.assertEqual(info_where["current"]["release_id"], rel_b)
        self.assertEqual(info_where["current"]["profile"], "Core")

        # Rollback volta para rel_a
        buf_rb = io.StringIO()
        with mock.patch("sys.stdout", buf_rb):
            rc_rb = cli.main(["rollback", "--root", str(self.root)])
        self.assertEqual(rc_rb, 0)
        apos_rb = json.loads((self.root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(apos_rb["release_id"], rel_a)

        # Cria arquivo do usuário em data_root para provar que uninstall nunca o apaga
        self.data_root.mkdir(parents=True, exist_ok=True)
        doc_usuario = self.data_root / "meu-processo.md"
        doc_usuario.write_text("# Documento do usuário\n", encoding="utf-8")

        # Uninstall sem --yes pede confirmação e aborta se o usuário responder 'n'
        with self.assertRaises(ValueError):
            lifecycle.uninstall_managed(
                root=self.root,
                yes=False,
                keep_data=False,
                input_fn=lambda prompt: "n",
            )
        self.assertTrue(self.root.exists())

        # Uninstall com --yes remove a raiz gerenciada mas preserva data_root intacta
        res_un = lifecycle.uninstall_managed(
            root=self.root,
            yes=True,
            keep_data=False,
        )
        self.assertEqual(res_un["status"], "uninstalled")
        self.assertFalse(self.root.exists())
        self.assertTrue(doc_usuario.is_file())
        self.assertEqual(doc_usuario.read_text(encoding="utf-8"), "# Documento do usuário\n")

    def test_update_offline_falha_limpo_sem_alterar_current(self) -> None:
        """traslado update sem rede falha claramente sem alterar current.json."""
        rel_id = f"{__version__}-core"
        self._preparar_release_candidata(rel_id)
        lifecycle.setup_release(
            root=self.root,
            release_id=rel_id,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
        )
        antes = (self.root / "current.json").read_bytes()
        with self.assertRaises(ValueError) as ctx:
            lifecycle.update_release(
                root=self.root,
                yes=True,
                fetch_json=lambda url: (_ for _ in ()).throw(OSError("rede indisponível")),
            )
        self.assertIn("nada foi alterado", str(ctx.exception))
        self.assertEqual((self.root / "current.json").read_bytes(), antes)

    def test_instalacao_legada_windows_reconhecida_atalho_removido_e_raiz_preservada(self) -> None:
        """Item 8: reconhece %LOCALAPPDATA%\\Freoa\\MarkdownConverter (product: freoa-md-converter),
        remove atalho 'Freoa Markdown Converter', grava superseded-by-traslado.json e não apaga a raiz antiga."""
        self.legacy_app.mkdir(parents=True, exist_ok=True)
        (self.legacy_app / "current.json").write_text(
            json.dumps({"product": "freoa-md-converter", "version": "1.1.0", "release_id": "1.1.0-full"}),
            encoding="utf-8",
        )
        arquivo_antigo = self.legacy_app / "releases" / "1.1.0-full" / "marcador.txt"
        arquivo_antigo.parent.mkdir(parents=True, exist_ok=True)
        arquivo_antigo.write_text("preservar", encoding="utf-8")

        atalho_desktop = self.desktop_dir / "Freoa Markdown Converter.lnk"
        atalho_menu = self.start_menu_dir / "Freoa Markdown Converter.lnk"
        atalho_desktop.write_bytes(b"fake-lnk")
        atalho_menu.write_bytes(b"fake-lnk")

        rel_id = f"{__version__}-core"
        self._preparar_release_candidata(rel_id)
        lifecycle.setup_release(
            root=self.root,
            release_id=rel_id,
            profile="Core",
            skip_ocr=True,
            no_shortcut=True,
            promote=True,
            allow_offline_promotion=True,
            data_root=self.data_root,
            platform_name="win32",
            desktop_dir=self.desktop_dir,
            start_menu_dir=self.start_menu_dir,
        )

        # Atalhos antigos removidos
        self.assertFalse(atalho_desktop.exists())
        self.assertFalse(atalho_menu.exists())

        # superseded-by-traslado.json gravado com versão, data e nova raiz
        superseded_path = self.legacy_app / "superseded-by-traslado.json"
        self.assertTrue(superseded_path.is_file())
        superseded = json.loads(superseded_path.read_text(encoding="utf-8"))
        self.assertEqual(superseded["product"], "traslado")
        self.assertEqual(superseded["version"], __version__)
        self.assertEqual(Path(superseded["new_root"]), self.root.resolve())
        self.assertIn("date", superseded)

        # Raiz antiga NÃO foi deletada
        self.assertTrue(self.legacy_app.is_dir())
        self.assertTrue(arquivo_antigo.is_file())

    def test_7zip_ausente_da_instalacao_ao_final(self) -> None:
        """Item 9: 7z.exe, 7z.dll, 7zr.exe, 7z2603-x64.exe, .bootstrap-tools e .ocr-download-cache
        são expurgados da instalação ao final, preservando 7ZIP-LICENSE.txt."""
        rel_dir = self.root / "releases" / f"{__version__}-full"
        ocr_dir = rel_dir / "ocr"
        doc_dir = ocr_dir / "doc"
        tools_dir = ocr_dir / ".bootstrap-tools"
        cache_dir = rel_dir / ".ocr-download-cache"
        for d in (doc_dir, tools_dir, cache_dir):
            d.mkdir(parents=True, exist_ok=True)

        (ocr_dir / "tesseract.exe").write_bytes(b"MZfake-tesseract")
        (doc_dir / "7ZIP-LICENSE.txt").write_text("GNU LGPL + unRAR restriction\n", encoding="utf-8")
        (tools_dir / "7z.exe").write_bytes(b"MZfake-7z")
        (tools_dir / "7z.dll").write_bytes(b"MZfake-7zdll")
        (ocr_dir / "7z.exe").write_bytes(b"MZfake-7z")
        (ocr_dir / "7z.dll").write_bytes(b"MZfake-7zdll")
        (ocr_dir / "7zr.exe").write_bytes(b"MZfake-7zr")
        (cache_dir / "7z2603-x64.exe").write_bytes(b"MZfake-installer")

        lifecycle._purge_seven_zip(self.root)

        restantes = {p.name.lower() for p in self.root.rglob("*")}
        for proibido in ("7z.exe", "7z.dll", "7zr.exe", "7z2603-x64.exe", ".bootstrap-tools", ".ocr-download-cache"):
            self.assertNotIn(proibido.lower(), restantes)
        self.assertTrue((ocr_dir / "tesseract.exe").is_file())
        self.assertTrue((doc_dir / "7ZIP-LICENSE.txt").is_file())

    @unittest.skipUnless(os.name == "nt", "teste de PowerShell install.ps1 específico do Windows")
    def test_bootstrap_install_ps1_com_fromdir_e_traslado_test_uv(self) -> None:
        """Item 1: executa install.ps1 -FromDir com TRASLADO_TEST_UV apontando para mock local sem rede."""
        if not (ROOT / "release-files.txt").is_file():
            self.skipTest("modo instalado: sem release-files.txt para o build local; o E2E cobre o bootstrap real")
        from_dir = self.tmp / "from_dir"
        from_dir.mkdir(parents=True, exist_ok=True)

        # Prepara artefatos mínimos no FromDir: wheel sintético + locks
        wheel_path = from_dir / f"traslado-{__version__}-py3-none-any.whl"
        with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for arq in (ROOT / "src" / "traslado").rglob("*"):
                if arq.is_file() and "__pycache__" not in arq.parts:
                    rel = arq.relative_to(ROOT / "src").as_posix()
                    zf.writestr(rel, arq.read_bytes())
            for arq in (ROOT / "src" / "freoa_v6").rglob("*"):
                if arq.is_file() and "__pycache__" not in arq.parts:
                    rel = arq.relative_to(ROOT / "src").as_posix()
                    zf.writestr(rel, arq.read_bytes())
            zf.writestr(
                f"traslado-{__version__}.dist-info/METADATA",
                f"Metadata-Version: 2.1\nName: traslado\nVersion: {__version__}\n",
            )
        for perfil in ("core", "full", "legacy"):
            shutil.copyfile(
                ROOT / "locks" / f"requirements-{perfil}.hashes.lock",
                from_dir / f"requirements-{perfil}.hashes.lock",
            )
        sums_lines = []
        for p in sorted(from_dir.iterdir()):
            if p.is_file():
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
                sums_lines.append(f"{digest}  {p.name}\n")
        (from_dir / "SHA256SUMS").write_text("".join(sums_lines), encoding="utf-8", newline="\n")

        # Cria mock de uv (mock-uv.cmd + mock_uv.py) que constrói o .venv local sem rede
        mock_uv_py = self.tmp / "mock_uv.py"
        mock_uv_py.write_text(
            "import os, shutil, subprocess, sys, zipfile\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "if os.environ.get('E5_UV_LOG'):\n"
            "    with open(os.environ['E5_UV_LOG'], 'a') as log: log.write(repr(args)+'\\n')\n"
            "if args and args[0] == 'venv':\n"
            "    venv_dir = Path(args[1])\n"
            f"    subprocess.run([r'{sys.executable}', '-m', 'venv', '--without-pip', '--system-site-packages', str(venv_dir)], check=True)\n"
            "elif len(args) >= 2 and args[0] == 'pip' and args[1] == 'install':\n"
            "    py_idx = args.index('--python')\n"
            "    venv_py = Path(args[py_idx + 1])\n"
            "    venv_dir = venv_py.parent.parent\n"
            "    site_dir = venv_dir / 'Lib' / 'site-packages'\n"
            "    alvo = args[-1]\n"
            "    if alvo.endswith('.whl'):\n"
            "        with zipfile.ZipFile(alvo) as zf:\n"
            "            zf.extractall(site_dir)\n"
            "        shutil.copyfile(venv_py, venv_py.parent / 'traslado.exe')\n"
            ,
            encoding="utf-8",
        )
        mock_uv_cmd = self.tmp / "mock-uv.cmd"
        mock_uv_cmd.write_text(
            f'@echo off\r\n"{sys.executable}" -X utf8 "{mock_uv_py}" %*\r\n',
            encoding="utf-8",
        )

        gh_stub = self.tmp / "gh.cmd"
        gh_stub.write_text('@echo off\r\necho %~nx3 >> "%E5_GH_LOG%"\r\nif "%~nx3"=="%E5_ATTESTATION_FAIL%" exit /b 1\r\nexit /b 0\r\n', encoding="ascii")
        install_root = self.tmp / "install_root"
        ps_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        env = {
            **os.environ,
            "TRASLADO_TEST_UV": str(mock_uv_cmd),
            "PATH": str(self.tmp) + os.pathsep + os.environ.get("PATH", ""),
            "E5_GH_LOG": str(self.tmp / "gh.log"),
            "E5_UV_LOG": str(self.tmp / "uv.log"),
            "TRASLADO_SHORTCUT_DESKTOP_DIR": str(self.desktop_dir),
            "TRASLADO_SHORTCUT_START_MENU_DIR": str(self.start_menu_dir),
            "TRASLADO_LEGACY_ROOT": str(self.legacy_app),
        }
        proc = subprocess.run(
            [
                str(ps_exe),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "install.ps1"),
                "-Profile",
                "Core",
                "-NoShortcut",
                "-AllowOfflinePromotion",
                "-FromDir",
                str(from_dir),
                "-Root",
                str(install_root),
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertTrue((install_root / "current.json").is_file())
        current = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["profile"], "Core")
        self.assertEqual(current["gates"], {"doctor": True, "fixture": True, "revocation": "offline", "offline_reason": "teste"})
        verified = (self.tmp / "gh.log").read_text().splitlines()
        self.assertEqual(set(line.strip() for line in verified), {wheel_path.name, "requirements-core.hashes.lock", "SHA256SUMS"})
        transmitted = list((install_root / "state/downloads").rglob("verified-*.json"))
        digests = json.loads(transmitted[0].read_text(encoding="utf-8-sig"))
        self.assertEqual(digests[wheel_path.name], hashlib.sha256(wheel_path.read_bytes()).hexdigest())
        self.assertIn("requirements-core.hashes.lock", digests)
        before = (install_root / "current.json").read_bytes()
        folders = list((install_root / "releases").iterdir())
        repeat = subprocess.run(proc.args, env=env, capture_output=True, timeout=120)
        self.assertEqual(repeat.returncode, 0, repeat.stdout+repeat.stderr)
        self.assertEqual((install_root / "current.json").read_bytes(), before)
        self.assertEqual(list((install_root / "releases").iterdir()), folders)

        # Each attestation failure aborts before uv creates a venv or installs dependencies.
        for artifact in (wheel_path.name, "requirements-core.hashes.lock", "requirements-legacy.hashes.lock", "SHA256SUMS"):
            args = list(proc.args)
            failed_root = self.tmp / ("failed-" + artifact)
            args[-1] = str(failed_root)
            if artifact == "requirements-legacy.hashes.lock":
                args += ["-Extras", "legacy"]
            uv_before = (self.tmp / "uv.log").read_bytes()
            failed = subprocess.run(args, env=dict(env, E5_ATTESTATION_FAIL=artifact), capture_output=True, timeout=120)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual((self.tmp / "uv.log").read_bytes(), uv_before)
            self.assertFalse((failed_root / "current.json").exists())

        # N10: a rejected gate removes the candidate after its interpreter exits;
        # --keep-failed retains the release for diagnosis.
        gate_dir = self.tmp / "rejected-gate"
        shutil.copytree(from_dir, gate_dir)
        with zipfile.ZipFile(wheel_path) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        entries["traslado/lifecycle.py"] += b"\nrun_fixture_gate = lambda **kwargs: False\n"
        with zipfile.ZipFile(gate_dir / wheel_path.name, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        (gate_dir / "SHA256SUMS").write_text("".join(
            hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n"
            for p in sorted(gate_dir.iterdir()) if p.name != "SHA256SUMS"), encoding="utf-8")
        for keep in (False, True):
            failed_root = self.tmp / ("gate-failed-"+str(keep))
            gate_args = list(proc.args)
            gate_args[gate_args.index("-FromDir")+1] = str(gate_dir)
            gate_args[gate_args.index("-Root")+1] = str(failed_root)
            if keep:
                gate_args.append("-KeepFailed")
            rejected = subprocess.run(gate_args, env=env, capture_output=True, timeout=120)
            self.assertEqual(rejected.returncode, 2, rejected.stdout+rejected.stderr)
            self.assertFalse((failed_root / "current.json").exists())
            self.assertEqual(bool(list((failed_root / "releases").iterdir())), keep, rejected.stdout+rejected.stderr)

        # Extract only the real build ZIP; no external SHA256SUMS accompanies it.
        dist = self.tmp / "built"
        built = subprocess.run([sys.executable, str(ROOT / "scripts/build_release.py"), "--permitir-sujo", "--dist", str(dist)], capture_output=True, timeout=180)
        self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
        extracted = self.tmp / "zip-only"
        with zipfile.ZipFile(dist / f"traslado-{__version__}-windows.zip") as archive:
            archive.extractall(extracted)
        payload = extracted / f"traslado-{__version__}-windows"
        self.assertTrue((payload / "PAYLOAD-SHA256SUMS").is_file())
        self.assertFalse((payload / "SHA256SUMS").exists())
        zip_root = self.tmp / "zip-install"
        wrapper = subprocess.run(["cmd.exe", "/c", str(payload / "INSTALAR.bat"), "-Root", str(zip_root), "-Profile", "Core", "-NoShortcut", "-AllowOfflinePromotion"], input=b"\n", env=env, capture_output=True, timeout=120)
        self.assertEqual(wrapper.returncode, 0, wrapper.stdout+wrapper.stderr)
        self.assertEqual(plat.read_current(zip_root)["gates"]["revocation"], "offline")



class LocksAndWrappersContractTests(unittest.TestCase):
    """Verifica os locks universais por perfil e os cabeçalhos das alternativas clicáveis."""

    def test_locks_universais_presentes_e_integros(self) -> None:
        spec_full = {
            l.strip().lower()
            for l in (ROOT / "requirements-full.lock").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")
        }
        full_in = {
            l.strip().lower()
            for l in (ROOT / "locks" / "full.in").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")
        }
        self.assertEqual(full_in, spec_full)

        core_lock = (ROOT / "locks" / "requirements-core.hashes.lock").read_text(encoding="utf-8")
        self.assertNotIn("pymupdf", core_lock.lower())

        full_lock = (ROOT / "locks" / "requirements-full.hashes.lock").read_text(encoding="utf-8")
        for req in spec_full:
            self.assertIn(req, full_lock.lower())
        self.assertIn("--hash=sha256:", full_lock)

        legacy_lock = (ROOT / "locks" / "requirements-legacy.hashes.lock").read_text(encoding="utf-8")
        self.assertIn("firecrawl-anydoc==0.2.4", legacy_lock.lower())
        self.assertIn("--hash=sha256:", legacy_lock)

    def test_wrappers_clicaveis_contem_spdx_e_link_da_arvore(self) -> None:
        url_esperada = f"https://github.com/rickfreoa/traslado/tree/v{__version__}"
        for nome in ("INSTALAR.bat", "instalar.command", "instalar.sh", "install.ps1", "install.sh"):
            texto = (ROOT / nome).read_text(encoding="utf-8")
            self.assertIn("SPDX-License-Identifier: AGPL-3.0-or-later", texto, nome)
            self.assertIn(url_esperada, texto, nome)


if __name__ == "__main__":
    unittest.main()
