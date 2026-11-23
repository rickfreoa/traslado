# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Cenários de caracterização de `traslado.lifecycle` (E1 da 3.0.0).

Regras, para que a caracterização valha igualmente antes e depois da decomposição:
- só se chama a fachada `traslado.lifecycle` (nomes públicos e privados que já existiam);
- dependências são injetadas pelos parâmetros da própria API (platform_name, env, fetch_*, run_cmd,
  doctor_fn, fixture_fn, revocation_fn, execute_fn) ou substituídas em objetos globais estáveis
  (`subprocess.run`, `shutil.which`, `traslado.revocation.verify_promotion`), nunca por atributo
  de `traslado.lifecycle`, cuja localização interna muda com a decomposição;
- toda saída é normalizada (raiz temporária, interpretador, arquitetura, separador de caminho) para que
  o mesmo esperado valha em Linux, macOS e Windows.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import platform as host
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from traslado import __version__, lifecycle, revocation

AGORA = "2026-11-23T12:00:00Z"
CENARIOS: dict[str, object] = {}


def cenario(func):
    CENARIOS[func.__name__] = func
    return func


# ---------------------------------------------------------------- normalização

class Normalizador:
    def __init__(self, tmp: Path):
        import traslado
        bases = {str(tmp), str(tmp.resolve())}
        pacote = Path(traslado.__file__).resolve().parent
        self.trocas = sorted(({b: "<TMP>" for b in bases} | {
            str(pacote): "<PKG>", str(Path(traslado.__file__).parent): "<PKG>",
            str(Path(sys.executable).resolve()): "<PY>", str(Path(sys.executable)): "<PY>",
            str(Path(tempfile.gettempdir()).resolve()): "<SYSTMP>", str(Path(tempfile.gettempdir())): "<SYSTMP>",
        }).items(), key=lambda kv: -len(kv[0]))
        self.arch = host.machine()

    def texto(self, s: str) -> str:
        # Só o separador de caminho é normalizado: cada barra invertida vira uma barra, sem colapsar sequências
        # (escapes de comando continuam distinguíveis; o Exec de .desktop é desfeito antes, em `desktop`).
        for velho, novo in self.trocas:
            for v in (velho, velho.replace("\\", "/")):
                s = s.replace(v, novo)
        s = s.replace("\\", "/")
        for velho, novo in self.trocas:
            s = s.replace(velho.replace("\\", "/"), novo)
        s = re.sub(r"<SYSTMP>/traslado-ocr-cache-[^\s\"',]+", "<CACHE-TEMP>", s)
        s = re.sub(r"<SYSTMP>/traslado-update-[^\s\"',/]+", "<UPDATE-TEMP>", s)
        return s

    def valor(self, v, chave=None):
        if isinstance(v, dict):
            return {str(k): self.valor(x, k) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
        if isinstance(v, (list, tuple)):
            return [self.valor(x) for x in v]
        if isinstance(v, Path):
            v = str(v)
        if isinstance(v, str):
            if chave == "arch" and v == self.arch:
                return "<ARCH>"
            if chave == "install_id" and re.fullmatch(r"[0-9a-f]{32}", v):
                return "<INSTALL-ID>"
            return self.texto(v)
        return v


def argv_normalizado(n: Normalizador, argv) -> list:
    argv = [str(a) for a in argv]
    if argv and argv[0].lower().endswith("powershell.exe"):
        argv[0] = "powershell.exe"
    return n.valor(argv)


def erro(n: Normalizador, exc: BaseException) -> dict:
    return {"erro": type(exc).__name__, "mensagem": n.texto(str(exc))}


def arvore(n: Normalizador, raiz: Path) -> dict:
    """Arquivos sob `raiz`: JSON por conteúdo normalizado; release-integrity só pelas chaves; demais por tamanho."""
    out = {}
    if not raiz.exists():
        return out
    for p in sorted(raiz.rglob("*")):
        rel = p.relative_to(raiz).as_posix()
        if "__pycache__" in p.parts or p.name.endswith(".lock") and "state" in p.parts:
            continue
        if p.is_dir():
            out[rel + "/"] = "dir"
        elif p.name == "release-integrity.json":
            out[rel] = {"chaves": sorted(json.loads(p.read_text(encoding="utf-8")))}
        elif p.suffix == ".json":
            try:
                out[rel] = n.valor(json.loads(p.read_text(encoding="utf-8-sig")))
            except ValueError:
                out[rel] = {"bytes": p.stat().st_size}
        elif p.suffix == ".desktop":
            out[rel] = desktop(n, p.read_text(encoding="utf-8"))
        elif p.suffix in {".txt", ".sh", ".ps1"}:
            out[rel] = n.texto(p.read_text(encoding="utf-8", errors="replace"))
        else:
            out[rel] = {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    return out


def desfazer_exec(valor: str) -> str:
    """Inverso de platform._desktop_exec: primeiro o escape geral do Desktop Entry, depois o do Exec."""
    geral, i = [], 0
    while i < len(valor):
        if valor[i] == "\\" and i + 1 < len(valor):
            geral.append({"\\": "\\", "s": " ", "n": "\n", "t": "\t", "r": "\r"}.get(valor[i + 1], "\\" + valor[i + 1]))
            i += 2
        else:
            geral.append(valor[i])
            i += 1
    texto, out, i = "".join(geral), [], 0
    while i < len(texto):
        if texto[i] == "\\" and i + 1 < len(texto) and texto[i + 1] in '\\"`$':
            out.append(texto[i + 1])
            i += 2
        else:
            out.append(texto[i])
            i += 1
    return "".join(out).replace("%%", "%")


def desktop(n: Normalizador, texto: str) -> list:
    """Linhas do .desktop; a linha Exec é registrada com o alvo já sem escapes (o escape varia com o SO)."""
    linhas = []
    for linha in texto.splitlines():
        m = re.fullmatch(r'Exec="((?:[^"\\]|\\.)*)"(.*)', linha)
        linhas.append(["Exec", n.texto(desfazer_exec(m.group(1))), m.group(2)] if m else n.texto(linha))
    return linhas


def chamar(n: Normalizador, func, *args, **kwargs) -> dict:
    so, se = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        try:
            r = {"retorno": n.valor(func(*args, **kwargs))}
        except Exception as exc:  # noqa: BLE001 - a caracterização grava o erro
            r = erro(n, exc)
    if so.getvalue():
        r["stdout"] = n.texto(so.getvalue())
    if se.getvalue():
        r["stderr"] = n.texto(se.getvalue())
    return r


def executar(nome: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="trasl-carac-") as raw:
        tmp = Path(raw)
        return CENARIOS[nome](tmp, Normalizador(tmp))


def executar_todos() -> dict:
    return {nome: executar(nome) for nome in sorted(CENARIOS)}


# ---------------------------------------------------------------- utilitários de montagem

def pe(machine: int) -> bytes:
    cab = bytearray(64)
    cab[:2] = b"MZ"
    cab[0x3C:0x40] = (64).to_bytes(4, "little")
    return bytes(cab) + b"PE\0\0" + machine.to_bytes(2, "little") + bytes(18)


def release_falsa(managed: Path, rid: str) -> Path:
    rel = managed / "releases" / rid
    for py in (rel / ".venv/bin/python", rel / ".venv/Scripts/python.exe"):
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"")
    return rel


def manifesto(managed: Path, rid: str, **extra) -> dict:
    base = {"schema_version": 2, "product": "traslado", "release_id": rid, "version": __version__,
            "profile": "Full", "extras": [], "languages": list(lifecycle.DEFAULT_LANGUAGES),
            "data_root": str(managed.parent / "dados"), "platform": "linux",
            "shortcuts": {"enabled": False, "home": None, "dirs": {}},
            "gates": {"doctor": True, "fixture": True, "revocation": "ok"}}
    base.update(extra)
    return base


def gravar(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def doctor_ok(*_a, **_k):
    return {"capabilities": {m: {"available": True} for m in ("fitz", "PIL", "docx", "openpyxl", "pptx")},
            "languages": list(lifecycle.DEFAULT_LANGUAGES)}


def digests():
    return {f"traslado-{__version__}-py3-none-any.whl": "a" * 64, "requirements-full.hashes.lock": "b" * 64,
            "requirements-core.hashes.lock": "c" * 64}


# ---------------------------------------------------------------- A. funções puras

@cenario
def a_validacoes_puras(tmp, n):
    r = {}
    for v in ("2.0.3", "3.0.0-rc1", "2.0.0.post1", "1.2", "v2.0.0", ""):
        r["validate_version:" + v] = chamar(n, lifecycle.validate_version, v)
    for v in (None, "por+eng", "POR, eng", ("spa", "ita"), "por,por", "xx", "", ["por+fra", "deu"]):
        r["normalize_languages:" + repr(v)] = chamar(n, lifecycle.normalize_languages, v)
    for v in (None, "", "legacy", "LEGACY+legacy", ["legacy"], "gui"):
        r["normalize_extras:" + repr(v)] = chamar(n, lifecycle.normalize_extras, v)
    r["compute_release_id"] = chamar(n, lifecycle.compute_release_id, "2.0.3", "Full", ["legacy"], ["por", "eng"], "s")
    r["compute_release_id_vazio"] = chamar(n, lifecycle.compute_release_id, "3.0.0", "Core", [], ["por"])
    for s in ("tesseract 5.5.3\n leptonica", "tesseract v4.1.1-rc", "TESSERACT 5", "nada"):
        r["_parse_tesseract_version:" + s] = chamar(n, lifecycle._parse_tesseract_version, s)
    r["DEFAULT_LANGUAGES"] = list(lifecycle.DEFAULT_LANGUAGES)
    r["REPOSITORY_URL"] = lifecycle.REPOSITORY_URL
    r["ShortcutError_mro"] = [c.__name__ for c in lifecycle.ShortcutError.__mro__]
    return r


@cenario
def a_dica_tesseract_linux(tmp, n):
    r = {}
    casos = {"ubuntu": 'ID=ubuntu\nID_LIKE=debian\n', "mint": 'ID=linuxmint\nID_LIKE="ubuntu debian"\n',
             "fedora": "ID=fedora\n", "opensuse": 'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n',
             "arch": "ID=arch\n", "alpine": "ID=alpine\n", "desconhecida": "ID=gentoo\n",
             "so_like": "ID=meu\nID_LIKE=rhel\n"}
    for nome, conteudo in casos.items():
        f = tmp / nome
        f.write_text(conteudo, encoding="utf-8")
        for langs in (None, "por+chi_sim", "eng"):
            r[f"{nome}:{langs}"] = chamar(n, lifecycle.linux_tesseract_hint, str(f), langs)
    r["ausente"] = chamar(n, lifecycle.linux_tesseract_hint, str(tmp / "nao-existe"), None)
    r["ausente_langs"] = chamar(n, lifecycle.linux_tesseract_hint, str(tmp / "nao-existe"), "por+eng")
    return r


@cenario
def a_gates_e_digests(tmp, n):
    r = {}
    caps = doctor_ok()
    sem_pptx = {"capabilities": {**caps["capabilities"], "pptx": {"available": False}}, "languages": ["por"]}
    for nome, rep, kw in (("ok_full", caps, {}), ("ok_full_ocr", caps, {"ocr_installed": True}),
                          ("sem_pptx_full", sem_pptx, {}), ("sem_pptx_core", sem_pptx, {"profile": "Core"}),
                          ("idioma_faltando", sem_pptx, {"profile": "Core", "ocr_installed": True, "languages": ["por", "eng"]}),
                          ("idioma_ok", sem_pptx, {"profile": "Core", "ocr_installed": True, "languages": "por"}),
                          ("nao_dict", [], {}), ("sem_caps", {"languages": []}, {})):
        r["doctor:" + nome] = chamar(n, lifecycle.evaluate_doctor_gate, rep, **kw)
    d = digests()
    for nome, mapa, perfil, extras in (("ok", d, "Full", []), ("ok_core", d, "Core", []),
                                       ("legacy_falta", d, "Full", ["legacy"]), ("none", None, "Full", []),
                                       ("hex_invalido", {**d, "x": "Z" * 64}, "Full", [])):
        r["digests:" + nome] = chamar(n, lifecycle.validate_bootstrap_digests, mapa, __version__, perfil, extras)
    env = {"TRASLADO_SHORTCUT_DESKTOP_DIR": str(tmp / "d"), "TRASLADO_SHORTCUT_ICONS_DIR": ""}
    r["shortcut_dirs_env"] = n.valor(lifecycle._shortcut_dirs_from_env(env))
    r["shortcut_dirs_param"] = n.valor(lifecycle._shortcut_dirs_from_env(env, start_menu_dir=tmp / "m", desktop_dir=tmp / "x"))
    r["shortcut_dirs_vazio"] = n.valor(lifecycle._shortcut_dirs_from_env({}))
    return r


@cenario
def a_arquitetura_pe(tmp, n):
    r = {}
    for nome, dados in (("x64", pe(0x8664)), ("arm64", pe(0xAA64)), ("x86", pe(0x014C)), ("outra", pe(0x1234)),
                        ("sem_mz", b"XX" + bytes(80)), ("curto", b"MZ"), ("sem_pe", bytes(pe(0x8664))[:64] + b"NOPE" + bytes(20))):
        f = tmp / (nome + ".exe")
        f.write_bytes(dados)
        r["machine:" + nome] = chamar(n, lifecycle._read_pe_machine, f)
        r["assert:" + nome] = chamar(n, lifecycle._assert_pe_architecture, f, "tesseract")
    r["plataforma_linux"] = chamar(n, lifecycle._assert_platform_architecture, "linux", {})
    r["plataforma_darwin"] = chamar(n, lifecycle._assert_platform_architecture, "darwin", {})
    return r


# ---------------------------------------------------------------- B. releases

@cenario
def b_remocao_e_poda(tmp, n):
    r = {}
    m = tmp / "managed"
    for rid in ("a", "b", "c", "d", "e"):
        release_falsa(m, rid)
    gravar(m / "current.json", manifesto(m, "a", previous_release_id="b"))
    gravar(m / "candidate.json", manifesto(m, "c", gates={"doctor": True, "fixture": True, "revocation": "ok"}))
    for rid in ("a", "b", "c", "d"):
        r["remove:" + rid] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / rid)
    r["fora_de_releases"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "outro")
    r["candidata_reprovada_por_parametro"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "c",
                                                    candidate_reprovada=True)
    release_falsa(m, "c")
    gravar(m / "candidate.json", manifesto(m, "c", gates={"doctor": True, "fixture": False, "revocation": "ok"}))
    r["candidata_reprovada_por_gate"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "c")
    release_falsa(m, "f")
    gravar(m / "current.json", manifesto(m, "a", previous_release_id="b", data_root=str(m / "releases" / "f" / "dados")))
    r["data_root_dentro"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "f")
    gravar(m / "state/pending-removals/e.json", {"release_id": "e"})
    release_falsa(m, "g")
    r["poda_pendentes"] = chamar(n, lifecycle.prune_releases, m, pending_only=True)
    r["arvore_pendentes"] = sorted(arvore(n, m / "releases"))
    r["poda_total"] = chamar(n, lifecycle.prune_releases, m)
    r["poda_sem_releases"] = chamar(n, lifecycle.prune_releases, tmp / "vazio")
    (m / "current.json").write_text("{corrompido", encoding="utf-8")
    release_falsa(m, "h")
    r["current_ilegivel"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "h")
    r["arvore"] = arvore(n, m)
    return r


@cenario
def b_integridade_e_release_ativa(tmp, n):
    r = {}
    m = tmp / "managed"
    rel = release_falsa(m, "r1")
    (rel / "x.txt").write_text("x", encoding="utf-8")
    (rel / "__pycache__").mkdir()
    (rel / "__pycache__" / "y.pyc").write_bytes(b"y")
    r["integridade"] = n.valor(lifecycle.release_integrity(rel))
    gravar(m / "current.json", manifesto(m, "r1"))
    r["sem_inventario"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full")
    gravar(rel / "release-integrity.json", lifecycle.release_integrity(rel))
    r["bate"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full")
    r["bate_online"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full", require_online=True)
    r["perfil_diferente"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Core")
    r["idiomas_diferentes"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full", None, "por")
    (rel / "x.txt").write_text("alterado", encoding="utf-8")
    r["adulterada"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full")
    gravar(m / "current.json", manifesto(m, "r1", gates={"revocation": "offline"}))
    r["offline_exige_online"] = chamar(n, lifecycle.active_release_matches, m, __version__, "Full", require_online=True)
    r["python_gerenciado"] = chamar(n, lifecycle._managed_python_exe, m)
    gravar(m / "current.json", manifesto(m, "r1", python=str(rel / ".venv/bin/python")))
    r["python_gerenciado_existe"] = chamar(n, lifecycle._managed_python_exe, m)
    return r


# ---------------------------------------------------------------- C. OCR

def lock_proprio(tmp: Path, conteudos: dict[str, bytes], declarado: dict[str, int] | None = None) -> Path:
    assets = [{"name": k, "url": "https://example.invalid/" + k, "bytes": (declarado or {}).get(k, len(v)),
               "sha256": hashlib.sha256(v).hexdigest()} for k, v in conteudos.items()]
    p = tmp / "lock.json"
    gravar(p, {"assets": assets})
    return p


@cenario
def c_tessdata_do_lock(tmp, n):
    r = {}
    dados = {"por.traineddata": b"P" * 10, "eng.traineddata": b"E" * 7, "TESSDATA-LICENSE.txt": b"lic"}
    lock = lock_proprio(tmp, dados)
    pedidos = []

    def busca(url):
        pedidos.append(url)
        return dados[url.rsplit("/", 1)[1]]
    r["ok"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr1", ["por", "eng"], lock, fetch_bytes=busca)
    r["pedidos"] = list(pedidos)
    r["arvore_ok"] = arvore(n, tmp / "ocr1")
    r["com_cache"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr2", ["por"], lock,
                            cache_dir=tmp / "cache", fetch_bytes=busca)
    r["arvore_cache"] = arvore(n, tmp / "cache")
    pedidos.clear()
    r["do_cache"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr3", ["por"], lock,
                           cache_dir=tmp / "cache", fetch_bytes=busca)
    r["pedidos_do_cache"] = list(pedidos)
    r["idioma_fora_do_lock"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr4", ["spa"], lock, fetch_bytes=busca)
    r["tamanho_divergente"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr5", ["por"], lock,
                                     fetch_bytes=lambda u: b"curto")
    r["sha_divergente"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr6", ["eng"], lock,
                                 fetch_bytes=lambda u: b"X" * len(dados[u.rsplit("/", 1)[1]]))
    lock2 = lock_proprio(tmp, dados, {"por.traineddata": 65 * 1024 * 1024})
    r["acima_do_limite"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr7", ["por"], lock2, fetch_bytes=busca)
    lock3 = lock_proprio(tmp, dados, {"por.traineddata": 0})
    r["declarado_zero"] = chamar(n, lifecycle._install_tessdata_from_lock, tmp / "ocr8", ["por"], lock3, fetch_bytes=busca)
    return r


@cenario
def c_setup_ocr_sem_ocr(tmp, n):
    rel = tmp / "rel"
    rel.mkdir()
    return {"core": chamar(n, lifecycle.setup_ocr, rel, profile="Core", platform_name="linux", env={}),
            "skip": chamar(n, lifecycle.setup_ocr, rel, skip_ocr=True, platform_name="win32", env={}),
            "env_skip": chamar(n, lifecycle.setup_ocr, rel, platform_name="darwin", env={"TRASLADO_SKIP_OCR": "1"}),
            "idiomas_invalidos": chamar(n, lifecycle.setup_ocr, rel, languages=["xx"], platform_name="linux", env={}),
            "lock_empacotado": n.texto(str(lifecycle._find_ocr_lock(rel, tmp))).rsplit("/", 2)[-2:],
            "bootstrap_empacotado": n.texto(str(lifecycle._find_bootstrap_ps1(rel, tmp))).rsplit("/", 2)[-2:]}


def windows_x64(func):
    """Simula Windows x64 nativo no módulo definidor de _get_windows_native_machine (independe do hospedeiro)."""
    def envolto(tmp, n):
        with no_modulo_definidor("_get_windows_native_machine", return_value=0x8664):
            return func(tmp, n)
    envolto.__name__ = func.__name__
    return envolto


@cenario
@windows_x64
def c_setup_ocr_windows(tmp, n):
    r = {}
    root, rel = tmp / "managed", tmp / "managed/releases/r1"
    ocr = rel / "ocr"
    (ocr / "tessdata").mkdir(parents=True)
    (ocr / "tesseract.exe").write_bytes(pe(0x8664))
    gravar(ocr / "ocr-runtime.json", {"version": "tesseract v5.5.9"})
    (root / "7z.exe").write_bytes(b"7")
    (rel / ".bootstrap-tools").mkdir()
    (rel / ".bootstrap-tools" / "7zr.exe").write_bytes(b"7")
    (rel / "sub").mkdir()
    (rel / "sub" / "7Z.DLL").write_bytes(b"7")
    env = {"PROCESSOR_ARCHITECTURE": "AMD64"}
    r["reaproveita"] = chamar(n, lifecycle.setup_ocr, rel, root=root, platform_name="win32", env=env)
    r["arvore_reaproveita"] = arvore(n, root)
    (ocr / "tesseract.exe").write_bytes(pe(0xAA64))
    r["reaproveita_arm64"] = chamar(n, lifecycle.setup_ocr, rel, root=root, platform_name="win32", env=env)
    shutil.rmtree(ocr)
    chamadas = []

    def roda(argv, **kw):
        chamadas.append([argv_normalizado(n, argv), sorted(k for k in kw)])
        Path(argv[argv.index("-Destination") + 1], "ocr-runtime.json").parent.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, roda.rc, b"", roda.err)
    roda.rc, roda.err = 0, b""
    with mock.patch.object(subprocess, "run", side_effect=roda):
        r["bootstrap_ok"] = chamar(n, lifecycle.setup_ocr, rel, root=root, platform_name="win32", env=env,
                                   languages="por,eng", ocr_cache=tmp / "cache")
        r["bootstrap_offline_cache_temporario"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="win32",
                                                         env={**env, "TRASLADO_OCR_OFFLINE": "1"}, languages=["deu"])
        (tmp / "from" / "ocr-cache").mkdir(parents=True)
        r["bootstrap_cache_do_from_dir"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="win32", env=env,
                                                  from_dir=tmp / "from")
        r["bootstrap_cache_por_env"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="win32",
                                              env={**env, "FREOA_OCR_TEST_CACHE": str(tmp / "envcache")})
        roda.rc, roda.err = 3, ("x" * 5000 + "falha final").encode()
        r["bootstrap_falha"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="win32", env=env)
        roda.err = None
        r["bootstrap_falha_sem_stderr"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="win32", env=env)
    r["chamadas"] = chamadas
    return r


@cenario
def c_setup_ocr_unix(tmp, n):
    r = {}
    rel = tmp / "rel"
    rel.mkdir()
    exe = tmp / "bin" / "tesseract"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    brew_prefix = tmp / "brew-tess"
    (brew_prefix / "bin").mkdir(parents=True)
    (brew_prefix / "bin" / "tesseract").write_text("#!/bin/sh\n", encoding="utf-8")
    (brew_prefix / "bin" / "tesseract").chmod(0o755)
    def which(nome, path=None):
        return {"tesseract": which.tess, "brew": which.brew}.get(nome)
    which.tess, which.brew = None, None
    def roda(argv, **kw):
        if argv[1:] == ["--prefix", "tesseract"]:
            return subprocess.CompletedProcess(argv, 0, str(brew_prefix) + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "tesseract 5.3.0\n")
    falha = lambda u: (_ for _ in ()).throw(OSError("rede vedada no teste"))
    with mock.patch.object(shutil, "which", side_effect=which), mock.patch.object(subprocess, "run", side_effect=roda):
        res = chamar(n, lifecycle.setup_ocr, rel, platform_name="linux", env={"PATH": ""})
        res["stdout"] = res.get("stdout", "").split(" Instale")[0].split(" Para instalar")[0]
        r["linux_sem_tesseract"] = res
        r["darwin_sem_tesseract"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="darwin", env={"PATH": ""})
        which.tess = str(exe)
        r["linux_com_tesseract_sem_rede"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="linux", env={"PATH": ""},
                                                   languages="por", fetch_bytes=falha)
        which.brew = "/opt/brew"
        r["darwin_homebrew_sem_rede"] = chamar(n, lifecycle.setup_ocr, rel, platform_name="darwin", env={"PATH": ""},
                                               languages="por", fetch_bytes=falha)
    return r


@cenario
def c_purga_7zip(tmp, n):
    for rel in ("7z.exe", "a/7z.dll", "a/b/7zr.exe", "7z2603-x64.exe", "manter.exe", ".ocr-download-cache/x",
                ".bootstrap-tools/y", "c/.bootstrap-tools/z"):
        p = tmp / "raiz" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"1")
    lifecycle._purge_seven_zip(tmp / "raiz")
    lifecycle._purge_seven_zip(tmp / "inexistente")
    return {"arvore": arvore(n, tmp / "raiz")}


# ---------------------------------------------------------------- D. gate de fixture e legado Windows

@cenario
def d_gate_de_fixture(tmp, n):
    r = {}
    vistos = []

    def exec_ok(fixture, out_dir, opts):
        vistos.append([Path(fixture).name, opts.mode, opts.chunks, opts.tesseract, opts.tessdata])
        saida = Path(out_dir) / Path(fixture).stem
        saida.mkdir(parents=True, exist_ok=True)
        for nome in ("document.md", "document.json", "manifest.json", "LEIA-AVISOS.txt", "chunks/chunks.jsonl"):
            (saida / nome).parent.mkdir(parents=True, exist_ok=True)
            (saida / nome).write_text("x", encoding="utf-8")
        return {"status": "complete", "output": str(saida)}

    def exec_sem_chunks(fixture, out_dir, opts):
        res = exec_ok(fixture, out_dir, opts)
        (Path(res["output"]) / "chunks/chunks.jsonl").unlink()
        return res
    r["full_ok"] = chamar(n, lifecycle.run_fixture_gate, execute_fn=exec_ok, tesseract="T", tessdata="D")
    r["full_chunks_ausentes"] = chamar(n, lifecycle.run_fixture_gate, execute_fn=exec_sem_chunks, chunks=True)
    r["full_chunks_ok"] = chamar(n, lifecycle.run_fixture_gate, execute_fn=exec_ok, chunks=True)
    r["status_falho"] = chamar(n, lifecycle.run_fixture_gate,
                               execute_fn=lambda f, o, p: {"status": "failed", "error_code": "x", "error": "e"})
    r["com_error_code"] = chamar(n, lifecycle.run_fixture_gate,
                                 execute_fn=lambda f, o, p: {"status": "complete", "error_code": "x", "output": "o"})
    r["nao_dict"] = chamar(n, lifecycle.run_fixture_gate, execute_fn=lambda f, o, p: None)
    r["excecao"] = chamar(n, lifecycle.run_fixture_gate,
                          execute_fn=lambda f, o, p: (_ for _ in ()).throw(RuntimeError("quebrou")))
    r["core_ok"] = chamar(n, lifecycle.run_fixture_gate, profile="Core", execute_fn=lambda f, o, p: exec_ok(f, o, p))
    r["vistos_full"] = vistos[:2]
    return r


@cenario
def d_legado_windows(tmp, n):
    r = {}
    novo = tmp / "Local" / "Traslado"
    legado = tmp / "Local" / "Freoa" / "MarkdownConverter"
    desk, menu = tmp / "Desktop", tmp / "Programs"
    for d in (desk, menu):
        d.mkdir(parents=True)
        (d / "Freoa Markdown Converter.lnk").write_bytes(b"lnk")
        (d / "Outro.lnk").write_bytes(b"lnk")
    kw = dict(platform_name="win32", home=tmp / "home", desktop_dir=desk, start_menu_dir=menu, now=AGORA)
    r["linux"] = chamar(n, lifecycle.handle_legacy_windows, novo, platform_name="linux", env={})
    r["sem_legado"] = chamar(n, lifecycle.handle_legacy_windows, novo, env={}, **kw)
    gravar(legado / "current.json", {"product": "outro", "release_id": "x"})
    r["produto_diferente"] = chamar(n, lifecycle.handle_legacy_windows, novo, env={}, **kw)
    gravar(legado / "current.json", {"product": "freoa-md-converter", "release_id": "1.9.0_abc"})
    r["legado_padrao"] = chamar(n, lifecycle.handle_legacy_windows, novo, env={}, version="9.9.9", **kw)
    outro = tmp / "outro-legado"
    gravar(outro / "current.json", {"product": "freoa-md-converter", "release_id": "1.8"})
    r["legado_por_env"] = chamar(n, lifecycle.handle_legacy_windows, novo, env={"TRASLADO_LEGACY_ROOT": str(outro)}, **kw)
    r["arvore"] = arvore(n, tmp)
    return r


# ---------------------------------------------------------------- E. setup_release

def _setup(n, tmp, **kw):
    base = dict(root=tmp / "managed", release_id="rel-1", platform_name="linux", env={"HOME": str(tmp / "home")},
                home=tmp / "home", skip_ocr=True, doctor_fn=doctor_ok, fixture_fn=lambda: True, now=AGORA,
                no_shortcut=True, expected_digests=digests(), data_root=tmp / "dados")
    base.update(kw)
    return chamar(n, lifecycle.setup_release, **base)


@cenario
def e_setup_candidata_e_promocao(tmp, n):
    r = {}
    revs = []

    def rev(root, version, dig, now=None):
        revs.append([n.texto(str(root)), version, sorted(dig), now])
        return {"status": "ok"}
    r["candidata"] = _setup(n, tmp)
    r["arvore_candidata"] = arvore(n, tmp / "managed")
    r["promove"] = _setup(n, tmp, release_id="rel-2", promote=True, revocation_fn=rev)
    r["revogacao_chamada"] = revs
    r["arvore_promovida"] = arvore(n, tmp / "managed")
    r["reconstruir_referenciada"] = _setup(n, tmp, release_id="rel-2", promote=True, revocation_fn=rev)
    r["segunda_promocao"] = _setup(n, tmp, release_id="rel-3", promote=True, revocation_fn=rev, languages="por",
                                   extras="legacy", profile="core",
                                   expected_digests={**digests(), "requirements-legacy.hashes.lock": "d" * 64})
    r["arvore_final"] = arvore(n, tmp / "managed")
    return r


@cenario
def e_setup_gates_reprovados(tmp, n):
    r = {}
    r["fixture_reprovada"] = _setup(n, tmp, release_id="f1", promote=True, fixture_fn=lambda: False,
                                    revocation_fn=lambda *a, **k: {"status": "ok"})
    r["fixture_reprovada_keep"] = _setup(n, tmp, release_id="f2", promote=True, fixture_fn=lambda: False, keep_failed=True)
    r["doctor_excecao"] = _setup(n, tmp, release_id="f3", promote=True,
                                 doctor_fn=lambda *a: (_ for _ in ()).throw(RuntimeError("doctor caiu")))
    r["fixture_excecao"] = _setup(n, tmp, release_id="f4", fixture_fn=lambda: (_ for _ in ()).throw(OSError("io")))
    r["revogacao_bloqueada"] = _setup(n, tmp, release_id="f5", promote=True,
                                      revocation_fn=lambda *a, **k: {"status": "blocked"})
    r["revogacao_excecao"] = _setup(n, tmp, release_id="f6", promote=True,
                                    revocation_fn=lambda *a, **k: (_ for _ in ()).throw(ValueError("canal divergente")))
    r["digests_incompletos"] = _setup(n, tmp, release_id="f7", promote=True, expected_digests={},
                                      revocation_fn=lambda *a, **k: {"status": "ok"})
    r["offline_sem_env"] = _setup(n, tmp, release_id="f8", promote=True, allow_offline_promotion=True)
    r["offline_teste"] = _setup(n, tmp, release_id="f9", promote=True, allow_offline_promotion=True,
                                env={"HOME": str(tmp / "home"), "TRASLADO_PROMOCAO_OFFLINE": "teste"})
    r["doctor_reprovado_core"] = _setup(n, tmp, release_id="f10", profile="Core", promote=True,
                                        doctor_fn=lambda *a: {"capabilities": "x"})
    r["arvore"] = arvore(n, tmp / "managed")
    return r


@cenario
def e_setup_raiz_de_dados_e_atalhos(tmp, n):
    r = {}
    env = {"HOME": str(tmp / "home"), "XDG_DATA_HOME": str(tmp / "xdg"),
           "TRASLADO_SHORTCUT_APPLICATIONS_DIR": str(tmp / "apps"), "TRASLADO_SHORTCUT_ICONS_DIR": str(tmp / "icons")}
    rev = lambda *a, **k: {"status": "ok"}
    r["raiz_padrao_home_documents"] = _setup(n, tmp, root=None, env=env, data_root=None, release_id="d1")
    r["raiz_custom_managed_data"] = _setup(n, tmp, root=tmp / "custom", env=env, data_root=None, release_id="d2")
    r["raiz_por_env"] = _setup(n, tmp, root=tmp / "custom", env={**env, "TRASLADO_DATA_ROOT": str(tmp / "envdata")},
                               data_root=None, release_id="d3")
    r["promove_com_atalhos"] = _setup(n, tmp, root=tmp / "sc", env=env, data_root=None, release_id="s1", promote=True,
                                      revocation_fn=rev, no_shortcut=False)
    r["herda_raiz_do_current"] = _setup(n, tmp, root=tmp / "sc", env=env, data_root=None, release_id="s2")
    r["arvore_sc"] = arvore(n, tmp / "sc")
    r["arvore_apps"] = arvore(n, tmp / "apps")
    r["arvore_icons"] = sorted(arvore(n, tmp / "icons"))
    r["xdg_padrao"] = sorted(arvore(n, tmp / "xdg"))
    r["home"] = sorted(arvore(n, tmp / "home"))
    return r


@cenario
def e_setup_windows_legado_sem_atalho(tmp, n):
    legado = tmp / "legado"
    gravar(legado / "current.json", {"product": "freoa-md-converter", "release_id": "1.9"})
    env = {"HOME": str(tmp / "home"), "TRASLADO_LEGACY_ROOT": str(legado),
           "TRASLADO_SHORTCUT_DESKTOP_DIR": str(tmp / "desk"), "TRASLADO_SHORTCUT_START_MENU_DIR": str(tmp / "menu")}
    (tmp / "desk").mkdir()
    (tmp / "desk" / "Freoa Markdown Converter.lnk").write_bytes(b"l")
    r = {"promove": _setup(n, tmp, platform_name="win32", env=env, release_id="w1", promote=True,
                           revocation_fn=lambda *a, **k: {"status": "ok"})}
    r["arvore"] = arvore(n, tmp)
    return r


# ---------------------------------------------------------------- F. uninstall e pendência

def _instalacao(tmp: Path, data_root: Path, rid="u1", platform="linux") -> Path:
    m = tmp / "managed"
    release_falsa(m, rid)
    (m / "python").mkdir(parents=True, exist_ok=True)
    (m / "tools").mkdir(exist_ok=True)
    (m / "state").mkdir(exist_ok=True)
    gravar(m / "current.json", manifesto(m, rid, data_root=str(data_root), platform=platform))
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "obra.md").write_text("dado", encoding="utf-8")
    return m


def _uninstall(n, tmp, m, **kw):
    base = dict(root=m, yes=True, platform_name="linux", env={}, home=tmp / "home",
                applications_dir=tmp / "apps", icons_dir=tmp / "icons")
    base.update(kw)
    return chamar(n, lifecycle.uninstall_managed, **base)


@cenario
def f_uninstall(tmp, n):
    r = {}
    (tmp / "apps").mkdir()
    (tmp / "apps" / "traslado.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    (tmp / "icons").mkdir()
    (tmp / "icons" / "traslado.png").write_bytes(b"png")
    m = _instalacao(tmp / "i1", tmp / "i1" / "dados")
    r["cancelado"] = _uninstall(n, tmp, m, yes=False, input_fn=lambda p: "n")
    r["cancelado_eof"] = _uninstall(n, tmp, m, yes=False, input_fn=lambda p: (_ for _ in ()).throw(EOFError()))
    r["confirmado_sim"] = _uninstall(n, tmp, m, yes=False, input_fn=lambda p: "Sim ")
    r["arvore_i1"] = arvore(n, tmp / "i1")
    r["atalhos_restantes"] = sorted(arvore(n, tmp / "apps")) + sorted(arvore(n, tmp / "icons"))
    m = _instalacao(tmp / "i2", tmp / "i2" / "managed" / "dados" / "fundo")
    r["dados_aninhados"] = _uninstall(n, tmp, m)
    r["arvore_i2"] = arvore(n, tmp / "i2")
    m = _instalacao(tmp / "i3", tmp / "i3" / "fora")
    r["keep_data"] = _uninstall(n, tmp, m, keep_data=True)
    r["arvore_i3"] = arvore(n, tmp / "i3")
    m = tmp / "i4" / "managed"
    m.mkdir(parents=True)
    r["sem_manifesto"] = _uninstall(n, tmp, m)
    gravar(m / "state/pending-uninstall.json", {"release_ids": [], "data_roots": [str(tmp / "i4" / "salvo")]})
    (m / "lixo").mkdir()
    r["por_pendencia"] = _uninstall(n, tmp, m)
    r["arvore_i4"] = arvore(n, tmp / "i4")
    m = _instalacao(tmp / "i5", tmp / "i5")
    r["dados_ancestrais"] = _uninstall(n, tmp, m)
    m = _instalacao(tmp / "i6", tmp / "i6" / "dados", platform="win32")
    r["windows_sem_uso"] = _uninstall(n, tmp, m, platform_name="win32", applications_dir=None, icons_dir=None,
                                      desktop_dir=tmp / "wd", start_menu_dir=tmp / "wm")
    r["arvore_i6"] = arvore(n, tmp / "i6")
    m = _instalacao(tmp / "i7", tmp / "i7" / "dados")
    gravar(m / "candidate.json", manifesto(m, "u1", data_root=str(tmp / "i7" / "outra")))
    (tmp / "i7" / "outra").mkdir()
    r["candidata_tambem_preservada"] = _uninstall(n, tmp, m)
    r["arvore_i7"] = arvore(n, tmp / "i7")
    return r


@cenario
def f_descarte_de_pendencia(tmp, n):
    m = tmp / "managed"
    for rid in ("p1", "p2", "ativa"):
        release_falsa(m, rid)
    gravar(m / "current.json", manifesto(m, "ativa"))
    gravar(m / "state/pending-uninstall.json", {"release_ids": ["p1", "ativa", "../fora", "inexistente"], "data_roots": []})
    r = {"descarta": chamar(n, lifecycle.discard_pending_uninstall, m), "arvore": arvore(n, m)}
    (m / "state/pending-uninstall.json").write_text("[1,2]", encoding="utf-8")
    r["marcador_lista"] = chamar(n, lifecycle.discard_pending_uninstall, m)
    r["sem_marcador"] = chamar(n, lifecycle.discard_pending_uninstall, tmp / "vazio")
    r["arvore_final"] = sorted(arvore(n, m))
    return r


# ---------------------------------------------------------------- G. update_release

def _dist(tmp: Path, nomes=("install.sh", "install.ps1"), wheels=("2.9.0",), adulterar=None) -> Path:
    d = tmp / "dist"
    d.mkdir(parents=True, exist_ok=True)
    for nome in nomes:
        (d / nome).write_text("# instalador " + nome + "\n", encoding="utf-8")
    linhas = [hashlib.sha256((d / x).read_bytes()).hexdigest() + "  " + x for x in nomes]
    linhas += ["%064x  *traslado-%s-py3-none-any.whl" % (i + 1, v) for i, v in enumerate(wheels)]
    linhas += ["lixo sem hash", "abc  curto"]
    (d / "SHA256SUMS").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    if adulterar:
        (d / adulterar).write_text("adulterado", encoding="utf-8")
    return d


@cenario
def g_update_local(tmp, n):
    r = {}
    m = tmp / "managed"
    gravar(m / "current.json", manifesto(m, "u1", profile="Core", extras=["legacy"], languages=["por", "fra"],
                                         shortcuts={"enabled": False}))
    chamadas = []

    def roda(argv, **kw):
        chamadas.append(argv_normalizado(n, argv))
        return subprocess.CompletedProcess(argv, roda.rc, roda.out, roda.err)
    roda.rc, roda.out, roda.err = 0, "", ""
    d = _dist(tmp)
    for plat_ in ("linux", "win32"):
        r["ok:" + plat_] = chamar(n, lifecycle.update_release, root=m, from_dir=d, yes=True, allow_offline_promotion=True,
                                  platform_name=plat_, env={}, run_cmd=roda)
    gravar(m / "current.json", manifesto(m, "u1", shortcuts={"enabled": True}))
    r["versao_explicita_com_atalhos"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, version="v2.9.0",
                                               platform_name="win32", env={}, run_cmd=roda)
    gravar(m / "current.json", manifesto(m, "u1", shortcuts=["legado.lnk"]))
    r["atalhos_esquema_1"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, platform_name="linux", env={}, run_cmd=roda)
    (m / "current.json").unlink()
    r["sem_current"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, platform_name="linux", env={}, run_cmd=roda)
    r["versao_ausente"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, version="3.0.0", platform_name="linux",
                                 env={}, run_cmd=roda)
    roda.rc, roda.err = 2, "  erro do instalador \n"
    r["instalador_falha"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, platform_name="linux", env={}, run_cmd=roda)
    roda.err, roda.out = "", "so stdout"
    r["instalador_falha_stdout"] = chamar(n, lifecycle.update_release, root=m, from_dir=d, platform_name="linux", env={},
                                          run_cmd=roda)
    r["chamadas"] = chamadas
    d2 = _dist(tmp / "dois", wheels=("2.9.0", "3.0.0"))
    r["dois_wheels"] = chamar(n, lifecycle.update_release, root=m, from_dir=d2, platform_name="linux", env={}, run_cmd=roda)
    d3 = _dist(tmp / "adult", adulterar="install.sh")
    r["hash_adulterado"] = chamar(n, lifecycle.update_release, root=m, from_dir=d3, platform_name="linux", env={}, run_cmd=roda)
    r["sem_artefatos"] = chamar(n, lifecycle.update_release, root=m, from_dir=tmp / "nada", platform_name="win32", env={},
                                run_cmd=roda)
    return r


@cenario
def g_update_online(tmp, n):
    r = {}
    m = tmp / "managed"
    rel = release_falsa(m, "u1")
    script = b"#!/bin/sh\necho instalador\n"
    sums = (hashlib.sha256(script).hexdigest() + "  install.sh\n" + hashlib.sha256(script).hexdigest()
            + "  install.ps1\n").encode()
    pedidos, chamadas, verificacoes = [], [], []

    def bytes_(url):
        pedidos.append(url)
        return sums if url.endswith("SHA256SUMS") else script

    def roda(argv, env=None, **kw):
        chamadas.append({"argv": argv_normalizado(n, argv),
                         "env": n.valor({k: v for k, v in (env or {}).items()
                                         if k.startswith("TRASLADO_SHORTCUT_") or k in {"HOME", "USERPROFILE"}})})
        return subprocess.CompletedProcess(argv, roda.rc, "", roda.err)
    roda.rc, roda.err = 0, ""

    def verifica(root, version, digs, **kw):
        verificacoes.append([n.texto(str(root)), version, n.valor(digs), sorted(kw)])
        return {"status": "ok"}
    env = {"TRASLADO_SHORTCUT_ICONS_DIR": "herdado-do-ambiente", "HOME": "outro"}
    r["sem_current"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                              fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_, gh_cmd=False, run_cmd=roda)
    gravar(m / "current.json", manifesto(m, "u1", profile="Core", extras=["legacy"], languages=["por"],
                                         data_root=str(tmp / "dados"), python=str(rel / ".venv/bin/python"),
                                         shortcuts={"enabled": True, "home": str(tmp / "h"),
                                                    "dirs": {"desktop_dir": str(tmp / "desk"), "icons_dir": None}}))
    with mock.patch.object(revocation, "verify_promotion", side_effect=verifica):
        r["rede_indisponivel"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                                        fetch_json=lambda u: (_ for _ in ()).throw(OSError("offline")),
                                        fetch_bytes=bytes_, gh_cmd=False, run_cmd=roda)
        r["versao_invalida_param"] = chamar(n, lifecycle.update_release, root=m, version="x", platform_name="linux", env=env,
                                            fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_, run_cmd=roda)
        r["tag_invalida"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                                   fetch_json=lambda u: {"tag_name": "latest"}, fetch_bytes=bytes_, run_cmd=roda)
        r["download_falha"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                                     fetch_json=lambda u: {"tag_name": "v2.9.0"},
                                     fetch_bytes=lambda u: (_ for _ in ()).throw(OSError("404")), run_cmd=roda)
        r["script_divergente"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                                        fetch_json=lambda u: {"tag_name": "v2.9.0"},
                                        fetch_bytes=lambda u: sums if u.endswith("SUMS") else b"outro", run_cmd=roda)
        r["nome_duplicado"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env=env,
                                     fetch_json=lambda u: {"tag_name": "v2.9.0"},
                                     fetch_bytes=lambda u: sums + sums if u.endswith("SUMS") else script, run_cmd=roda)
        for plat_ in ("linux", "win32"):
            r["ok:" + plat_] = chamar(n, lifecycle.update_release, root=m, version="2.9.0", yes=True, platform_name=plat_,
                                      env=env, fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_,
                                      gh_cmd="gh-stub", run_cmd=roda)
        gravar(m / "current.json", manifesto(m, "u1", data_root=str(tmp / "dados"), python=str(rel / ".venv/bin/python")))
        roda.rc = 4
        r["so_atalhos_falharam"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env={},
                                          fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_, gh_cmd=False,
                                          run_cmd=roda)
        gravar(m / "current.json", manifesto(m, "u1", data_root=str(tmp / "dados")))
        r["so_atalhos_sem_python"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env={},
                                            fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_, gh_cmd=False,
                                            run_cmd=roda)
        roda.rc, roda.err = 1, "quebrou"
        r["instalador_falha"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env={},
                                       fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_, gh_cmd=False,
                                       run_cmd=roda)
        roda.rc = 1
        r["attestation_reprovada"] = chamar(n, lifecycle.update_release, root=m, platform_name="linux", env={},
                                            fetch_json=lambda u: {"tag_name": "v2.9.0"}, fetch_bytes=bytes_,
                                            gh_cmd="gh-stub", run_cmd=roda)
    r["pedidos"] = pedidos
    r["chamadas"] = chamadas
    r["verificacoes"] = verificacoes
    return r


# ---------------------------------------------------------------- H. superfície

@cenario
def h_superficie(tmp, n):
    return {"nomes": sorted(k for k in vars(lifecycle) if not k.startswith("__"))}


# ---------------------------------------------------------------- I. cenários da revisão do Codex (E1, tentativa 2)

def no_modulo_definidor(nome: str, **kw):
    """Substitui `nome` no módulo que o define (o próprio lifecycle antes da decomposição; o submódulo depois)."""
    return mock.patch.object(sys.modules[getattr(lifecycle, nome).__module__], nome, **kw)


@cenario
def i_setup_gate_padrao_e_ocr_ativo(tmp, n):
    r = {}
    # Gate de fixture padrão (run_fixture_gate real), perfil Core: converte a fixture DOCX ou, sem python-docx, a TXT.
    res = _setup(n, tmp, release_id="g1", profile="Core", fixture_fn=None)
    r["fixture_padrao_core"] = {"gates": res.get("retorno", {}).get("gates"), "erro": res.get("erro"),
                                "stderr": res.get("stderr")}
    # OCR ativo pelo setup_release (Windows, OCR já presente na release: sem subprocesso).
    rel = tmp / "managed" / "releases" / "o1"
    (rel / "ocr" / "tessdata").mkdir(parents=True)
    (rel / "ocr" / "tesseract.exe").write_bytes(pe(0x8664))
    gravar(rel / "ocr" / "ocr-runtime.json", {"version": "tesseract 5.5.3"})
    vistos = []

    def doctor(tess, tessdata):
        vistos.append([n.texto(str(tess)), n.texto(str(tessdata))])
        return doctor_ok()
    env = {"HOME": str(tmp / "home"), "PROCESSOR_ARCHITECTURE": "AMD64"}
    with no_modulo_definidor("_get_windows_native_machine", return_value=0x8664):
        r["ocr_ativo_windows"] = _setup(n, tmp, release_id="o1", platform_name="win32", env=env, skip_ocr=False,
                                        doctor_fn=doctor, languages="por,eng")
    r["doctor_recebeu"] = vistos
    r["arvore_o1"] = arvore(n, rel)
    return r


@cenario
def i_uninstall_windows_com_release_em_uso(tmp, n):
    r = {}
    m = _instalacao(tmp / "w", tmp / "w" / "dados", rid="w1", platform="win32")
    release_falsa(m, "w2")
    em_uso = {"w1"}
    proibido = AssertionError("remoção diferida real não pode ser lançada neste cenário")
    with no_modulo_definidor("release_in_use", side_effect=lambda p: Path(p).name in em_uso), \
         mock.patch.object(subprocess, "Popen", side_effect=proibido):
        r["primeira"] = _uninstall(n, tmp, m, platform_name="win32", applications_dir=None, icons_dir=None,
                                   desktop_dir=tmp / "wd", start_menu_dir=tmp / "wm")
        r["arvore_primeira"] = arvore(n, tmp / "w")
        em_uso.clear()
        r["segunda_pela_pendencia"] = _uninstall(n, tmp, m, platform_name="win32", applications_dir=None,
                                                 icons_dir=None, desktop_dir=tmp / "wd", start_menu_dir=tmp / "wm")
        r["arvore_segunda"] = arvore(n, tmp / "w")
    # Fora daqui: só o runtime em uso. Com os.name == "nt", isso é "preso só por este processo" e dispara a
    # remoção diferida real; o resultado depende do SO hospedeiro (a remoção diferida tem cenário próprio).
    return r


@cenario
def i_remocao_diferida(tmp, n):
    r = {}
    m = tmp / "managed"
    rel = release_falsa(m, "d1")
    dados = tmp / "dados"
    dados.mkdir()
    lancados = []

    def popen(argv, **kw):
        env = kw.get("env") or {}
        lancados.append({"argv0": Path(argv[0]).name.lower(), "argv": argv[1:-1],
                         "script": n.texto(base64.b64decode(argv[-1]).decode("utf-16le")),
                         "sem_janela": kw.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         "redirecionamentos": {k: (kw.get(k) is subprocess.DEVNULL) for k in ("stdin", "stdout", "stderr")},
                         "env": n.valor({k: v for k, v in env.items() if k.startswith("TRASLADO_REMOVE_") and k != "TRASLADO_REMOVE_PID"}),
                         "pid_e_o_proprio": env.get("TRASLADO_REMOVE_PID") == str(os.getpid()),
                         "kw": sorted(kw)})
        return mock.Mock()
    with mock.patch.object(subprocess, "Popen", side_effect=popen):
        r["ok"] = chamar(n, lifecycle._defer_self_removal, m, [rel, m / "python"], [dados])
        r["fora_da_raiz"] = chamar(n, lifecycle._defer_self_removal, m, [tmp / "outro"], [dados])
        r["a_propria_raiz"] = chamar(n, lifecycle._defer_self_removal, m, [m], [dados])
        r["contem_preservado"] = chamar(n, lifecycle._defer_self_removal, m, [m], [m / "dados"])
        r["vazio"] = chamar(n, lifecycle._defer_self_removal, m, [], [dados])
    r["lancados"] = lancados
    return r


@cenario
def i_lock_ocupado_e_falha_de_exclusao(tmp, n):
    r = {}
    m = tmp / "managed"
    for rid in ("x1", "x2", "atual"):
        release_falsa(m, rid)
    gravar(m / "current.json", manifesto(m, "atual"))
    (m / "state").mkdir(parents=True, exist_ok=True)
    (m / "state" / "promote.lock").write_text("123\n", encoding="utf-8")
    r["lock_ocupado"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "x1")
    (m / "state" / "promote.lock").unlink()
    with mock.patch.object(shutil, "rmtree", side_effect=OSError("arquivo preso")):
        r["exclusao_falha"] = chamar(n, lifecycle.remove_unreferenced_release, m, m / "releases" / "x2")
    r["arvore"] = arvore(n, m)
    r["poda_dos_pendentes"] = chamar(n, lifecycle.prune_releases, m, pending_only=True)
    r["arvore_final"] = sorted(arvore(n, m))
    return r


@cenario
def i_ligacoes_internas_do_setup(tmp, n):
    """Argumentos com que setup_release chama os gates e o tratamento do legado (espiões no módulo definidor)."""
    r, chamadas = {}, []
    rel = tmp / "managed" / "releases" / "l1"
    (rel / "ocr" / "tessdata").mkdir(parents=True)
    (rel / "ocr" / "tesseract.exe").write_bytes(pe(0x8664))

    def espiao(nome, retorno):
        def f(*a, **kw):
            chamadas.append([nome, n.valor(list(a)), n.valor({k: v for k, v in kw.items() if k != "now"})])
            return retorno
        return f
    env = {"HOME": str(tmp / "home"), "PROCESSOR_ARCHITECTURE": "AMD64",
           "TRASLADO_SHORTCUT_DESKTOP_DIR": str(tmp / "desk"), "TRASLADO_SHORTCUT_START_MENU_DIR": str(tmp / "menu")}
    with no_modulo_definidor("_get_windows_native_machine", return_value=0x8664), \
         no_modulo_definidor("run_fixture_gate", side_effect=espiao("run_fixture_gate", True)), \
         no_modulo_definidor("handle_legacy_windows", side_effect=espiao("handle_legacy_windows", [])), \
         no_modulo_definidor("evaluate_doctor_gate", side_effect=espiao("evaluate_doctor_gate", True)), \
         no_modulo_definidor("validate_bootstrap_digests", side_effect=espiao("validate_bootstrap_digests", {"d": "x"})):
        r["promove"] = _setup(n, tmp, release_id="l1", platform_name="win32", env=env, skip_ocr=False, fixture_fn=None,
                              promote=True, languages="por", revocation_fn=lambda *a, **k: {"status": "ok"})
    r["chamadas"] = chamadas
    return r
