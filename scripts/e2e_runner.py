# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Orquestrador E2E do Traslado 2.0.0 para Linux (contêineres limpos), Windows e macOS.

Executa e registra em JSON:
- Verificação de atalhos (.desktop/.png no Linux, .lnk no Windows, .app no macOS)
- traslado where --json
- Auditoria de bibliotecas carregadas (scripts/auditar_libs.py)
- Conversão Full de fixture.docx e fixture.pdf com --chunks
- Modo degradado sem OCR (PDF escaneado -> needs_ocr + instrução do gerenciador de pacotes)
- Geração de diretório local 2.0.0.post1 e execução de traslado update --from-dir
- Execução de traslado rollback para 2.0.0
- Ensaio de quarentena/zona (Zone.Identifier no Windows; com.apple.quarantine no macOS)
- Execução de traslado uninstall --yes e verificação de limpeza
"""
from __future__ import annotations

import argparse
import base64
import traceback
import hashlib
import io
import json
import os
import plistlib
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _sha256(data: bytes | Path) -> str:
    raw = data.read_bytes() if isinstance(data, Path) else data
    return hashlib.sha256(raw).hexdigest()


def _build_post1_release_dir(dist_dir: Path, post_dir: Path, target_version: str = "2.0.0.post1") -> Path:
    """Gera um diretório de release local 2.0.0.post1 a partir de dist/ para testar traslado update."""
    post_dir.mkdir(parents=True, exist_ok=True)
    orig_wheels = list(dist_dir.glob("traslado-*-py3-none-any.whl"))
    if not orig_wheels:
        raise RuntimeError(f"Nenhum wheel encontrado em {dist_dir}")
    orig_wheel = orig_wheels[0]
    m = re.match(r"^traslado-(.+)-py3-none-any\.whl$", orig_wheel.name)
    orig_version = m.group(1) if m else "2.0.0"

    new_wheel = post_dir / f"traslado-{target_version}-py3-none-any.whl"
    old_dist_info = f"traslado-{orig_version}.dist-info/"
    new_dist_info = f"traslado-{target_version}.dist-info/"

    record_lines: list[str] = []
    with zipfile.ZipFile(orig_wheel, "r") as zin, zipfile.ZipFile(new_wheel, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename.endswith(".dist-info/RECORD"):
                continue
            data = zin.read(item.filename)
            new_name = item.filename
            if new_name.startswith(old_dist_info):
                new_name = new_dist_info + new_name[len(old_dist_info) :]
            if new_name == "traslado/__init__.py":
                txt = data.decode("utf-8").replace(f'__version__ = "{orig_version}"', f'__version__ = "{target_version}"')
                data = txt.encode("utf-8")
            elif new_name.endswith(".dist-info/METADATA"):
                txt = re.sub(r"^Version:[ \t]*[^\r\n]+", f"Version: {target_version}", data.decode("utf-8"), flags=re.M)
                data = txt.encode("utf-8")
            zout.writestr(new_name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record_lines.append(f"{new_name},sha256={digest},{len(data)}")
        record_name = f"{new_dist_info}RECORD"
        record_lines.append(f"{record_name},,")
        zout.writestr(record_name, ("\n".join(record_lines) + "\n").encode("utf-8"))

    # Copiar locks e scripts atualizando a versão embutida
    artifacts: list[Path] = [new_wheel]
    for lock_name in ("requirements-core.hashes.lock", "requirements-full.hashes.lock", "requirements-legacy.hashes.lock"):
        src_lock = dist_dir / lock_name
        if not src_lock.is_file():
            src_lock = ROOT / "locks" / lock_name
        if src_lock.is_file():
            dst_lock = post_dir / lock_name
            shutil.copyfile(src_lock, dst_lock)
            artifacts.append(dst_lock)

    for extra_asset in ("ocr-assets.lock.json",):
        src_a = dist_dir / extra_asset if (dist_dir / extra_asset).is_file() else (ROOT / extra_asset)
        if src_a.is_file():
            shutil.copyfile(src_a, post_dir / extra_asset)
    if (ROOT / "scripts/bootstrap_ocr.ps1").is_file():
        (post_dir / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts/bootstrap_ocr.ps1", post_dir / "scripts/bootstrap_ocr.ps1")

    ps1_src = (dist_dir / "install.ps1" if (dist_dir / "install.ps1").is_file() else ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    ps1_out = re.sub(r'(\[string\]\$Version\s*=\s*)"[^"]+"', rf'\1"{target_version}"', ps1_src, count=1)
    ps1_dst = post_dir / "install.ps1"
    ps1_dst.write_text(ps1_out, encoding="utf-8", newline="\n")
    artifacts.append(ps1_dst)

    sh_src = (dist_dir / "install.sh" if (dist_dir / "install.sh").is_file() else ROOT / "install.sh").read_text(encoding="utf-8")
    sh_out = re.sub(r'(^VERSION=)"[^"]+"', rf'\1"{target_version}"', sh_src, count=1, flags=re.M)
    sh_dst = post_dir / "install.sh"
    sh_dst.write_text(sh_out, encoding="utf-8", newline="\n")
    artifacts.append(sh_dst)

    sums_lines = [f"{_sha256(p)}  {p.name}\n" for p in artifacts]
    (post_dir / "SHA256SUMS").write_text("".join(sums_lines), encoding="utf-8", newline="\n")
    return post_dir


def _sem_gh(env: dict) -> dict:
    """PATH sem o GitHub CLI: artefatos locais de CI não têm attestation de release.yml, e o instalador só a confere
    quando gh existe. O E2E de artefato publicado (E11) roda com gh e attestation real."""
    nomes = {"gh", "gh.exe"}
    partes = [p for p in env.get("PATH", "").split(os.pathsep)
              if p and not any((Path(p) / nome).is_file() for nome in nomes)]
    return {**env, "PATH": os.pathsep.join(partes)}


def _managed_python(managed_root: Path, release_id: str) -> Path:
    if os.name == "nt":
        return managed_root / "releases" / release_id / ".venv" / "Scripts" / "python.exe"
    return managed_root / "releases" / release_id / ".venv" / "bin" / "python"


def _create_scanned_pdf(dest: Path, py_exe: Path) -> Path:
    code = (
        "import fitz\n"
        "from PIL import Image, ImageDraw\n"
        "import io, sys\n"
        "img = Image.new('RGB', (600, 300), 'white')\n"
        "draw = ImageDraw.Draw(img)\n"
        "draw.text((40, 120), 'DOCUMENTO ESCANEADO SEM CAMADA DE TEXTO 12345', fill='black')\n"
        "buf = io.BytesIO()\n"
        "img.save(buf, format='PNG')\n"
        "doc = fitz.open()\n"
        "page = doc.new_page(width=600, height=300)\n"
        "page.insert_image(fitz.Rect(0, 0, 600, 300), stream=buf.getvalue())\n"
        "doc.save(sys.argv[1])\n"
    )
    subprocess.run([str(py_exe), "-I", "-c", code, str(dest)], check=True)
    return dest


def _verify_common_lifecycle(
    managed_root: Path,
    dist_dir: Path,
    work_dir: Path,
    env: dict[str, str],
    allowlist_path: Path,
    audit_output: Path,
    expect_degraded_ocr: bool,
) -> dict:
    report: dict = {}

    # 1. Ler current.json inicial e rodar traslado where --json
    current_path = managed_root / "current.json"
    if not current_path.is_file():
        raise RuntimeError(f"current.json não encontrado em {managed_root}")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    release_id = current["release_id"]
    py_exe = _managed_python(managed_root, release_id)
    if not py_exe.is_file():
        raise RuntimeError(f"Python gerenciado ausente: {py_exe}")

    where_proc = subprocess.run(
        [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "where", "--json", "--root", str(managed_root)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    where_data = json.loads(where_proc.stdout.strip())
    report["where_initial"] = where_data
    assert where_data["current"]["version"] == "2.0.0", where_data
    assert where_data["current"]["profile"] == "Full", where_data

    # 2. Auditoria de bibliotecas carregadas durante conversão Full
    audit_proc = subprocess.run(
        [
            str(py_exe),
            "-I",
            "-X",
            "utf8",
            str(ROOT / "scripts/auditar_libs.py"),
            "--root",
            str(managed_root),
            "--allowlist",
            str(allowlist_path),
            "--saida",
            str(audit_output),
        ],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if audit_proc.returncode != 0:
        raise RuntimeError(f"Auditoria de bibliotecas falhou:\n{audit_proc.stdout}\n{audit_proc.stderr}")
    report["library_audit"] = json.loads(audit_output.read_text(encoding="utf-8"))

    # 3. Conversão Full de fixture.docx e fixture.pdf com --chunks
    conv_out = work_dir / "conv-out"
    conv_results = {}
    for fx_name in ("fixture.docx", "fixture.pdf"):
        fx_path = ROOT / "src/traslado/assets" / fx_name
        dest_fx = conv_out / fx_name.replace(".", "_")
        proc = subprocess.run(
            [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "convert", str(fx_path), "-o", str(dest_fx), "--chunks"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode not in (0, 3):
            raise RuntimeError(f"Falha ao converter {fx_name}: rc={proc.returncode} stderr={proc.stderr} stdout={proc.stdout}")
        res_json = json.loads(proc.stdout.strip().splitlines()[-1])
        assert res_json.get("error_code") is None, res_json
        if res_json["status"] == "partial":
            assert res_json.get("warning_summary") == {"docx_formatting_partial": 1}, res_json
        out_pkg = Path(res_json["output"])
        doc_md = out_pkg / "document.md"
        manifest_json = out_pkg / "manifest.json"
        chunks_jsonl = out_pkg / "chunks" / "chunks.jsonl"
        assert doc_md.is_file() and doc_md.stat().st_size > 0, f"document.md inválido em {out_pkg}"
        expected = f"Traslado verificacao de instalacao {fx_path.suffix[1:].upper()} OK."
        assert expected in doc_md.read_text(encoding="utf-8"), f"Sentinela ausente: {fx_name}"
        assert manifest_json.is_file(), f"manifest.json ausente em {out_pkg}"
        assert chunks_jsonl.is_file() and chunks_jsonl.stat().st_size > 0, f"chunks.jsonl ausente em {out_pkg}"
        conv_results[fx_name] = {
            "status": res_json["status"],
            "document_md_bytes": doc_md.stat().st_size,
            "chunks_count": len(chunks_jsonl.read_text(encoding="utf-8").splitlines()),
        }
    report["conversions"] = conv_results

    # 4. Modo degradado sem OCR (quando aplicável ou com PATH limpo de tesseract)
    if expect_degraded_ocr:
        scan_pdf = _create_scanned_pdf(work_dir / "scan_sem_ocr.pdf", py_exe)
        no_ocr_env = dict(env)
        if sys.platform == "darwin":
            # Garante ausência de Homebrew/Tesseract no PATH para provar o modo degradado sem OCR
            no_ocr_env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        deg_out = work_dir / "degraded-out"
        deg_proc = subprocess.run(
            [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "convert", str(scan_pdf), "-o", str(deg_out)],
            env=no_ocr_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        deg_json = json.loads(deg_proc.stdout.strip().splitlines()[-1])
        assert deg_json.get("status") == "partial", deg_json
        assert deg_json.get("error_code") == "needs_ocr", deg_json
        assert deg_json.get("error_pages") == [1], deg_json
        err_msg = str(deg_json.get("error") or "")
        if sys.platform == "darwin":
            assert "brew install tesseract" in err_msg, deg_json
        else:
            assert "apt install tesseract-ocr" in err_msg or "dnf install tesseract" in err_msg, deg_json
        report["degraded_no_ocr"] = {
            "return_code": deg_proc.returncode,
            "status": deg_json.get("status"),
            "error_code": deg_json.get("error_code"),
            "error_pages": deg_json.get("error_pages"),
            "error": err_msg,
        }

    # 5. Update local de 2.0.0 para 2.0.0.post1 a partir de diretório local
    post1_dir = _build_post1_release_dir(dist_dir, work_dir / "dist-2.0.0.post1", "2.0.0.post1")
    upd_proc = subprocess.run(
        [
            str(py_exe),
            "-I",
            "-X",
            "utf8",
            "-m",
            "traslado",
            "update",
            "--from-dir",
            str(post1_dir),
            "--root",
            str(managed_root),
            "--yes",
            "--allow-offline-promotion",
        ],
        env=_sem_gh(env),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if upd_proc.returncode != 0:
        raise RuntimeError(f"Falha no traslado update local: {upd_proc.stdout}\n{upd_proc.stderr}")
    upd_data = json.loads(upd_proc.stdout.strip())
    after_upd = json.loads((managed_root / "current.json").read_text(encoding="utf-8"))
    assert after_upd["version"] == "2.0.0.post1", after_upd
    assert after_upd["previous_release_id"] == release_id, after_upd
    report["update_to_post1"] = {
        "command_result": upd_data,
        "active_version": after_upd["version"],
        "active_release_id": after_upd["release_id"],
        "previous_release_id": after_upd["previous_release_id"],
    }

    # 6. Rollback de 2.0.0.post1 de volta para 2.0.0
    post1_py = _managed_python(managed_root, after_upd["release_id"])
    rb_proc = subprocess.run(
        [str(post1_py), "-I", "-X", "utf8", "-m", "traslado", "rollback", "--root", str(managed_root)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    rb_data = json.loads(rb_proc.stdout.strip())
    after_rb = json.loads((managed_root / "current.json").read_text(encoding="utf-8"))
    assert after_rb["version"] == "2.0.0", after_rb
    assert after_rb["release_id"] == release_id, after_rb
    report["rollback_to_2_0_0"] = {
        "active_version": after_rb["version"],
        "active_release_id": after_rb["release_id"],
        "previous_release_id": rb_data.get("previous_release_id"),
    }

    return report


def run_linux_container_post_install(
    managed_root: Path,
    dist_dir: Path,
    audit_output: Path,
    pre_inventory_path: Path | None = None,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="traslado-e2e-linux-") as tmp:
        work_dir = Path(tmp)
        env = os.environ.copy()
        temp_dir = work_dir / "tmp"
        temp_dir.mkdir()
        env.update(TEMP=str(temp_dir), TMP=str(temp_dir), TMPDIR=str(temp_dir))
        env["TRASLADO_PROMOCAO_OFFLINE"] = "teste"

        report: dict = {"os": "linux", "mode": "linux-container"}
        if pre_inventory_path and pre_inventory_path.is_file():
            report["initial_container_inventory"] = json.loads(pre_inventory_path.read_text(encoding="utf-8"))

        # Verificar atalhos .desktop e .png criados pela instalação padrão
        current = json.loads((managed_root / "current.json").read_text(encoding="utf-8"))
        # current.json (esquema 2) guarda a configuração dos atalhos, não a lista de arquivos criados.
        config = current.get("shortcuts") or {}
        assert config.get("enabled") is True, f"Atalhos desativados no manifesto: {config}"
        home_sc, dirs = Path(config["home"]), config.get("dirs") or {}
        apps_dir = Path(dirs.get("applications_dir") or home_sc / ".local/share/applications")
        icons_dir = Path(dirs.get("icons_dir") or home_sc / ".local/share/icons/hicolor/256x256/apps")
        shortcuts = [apps_dir / "traslado.desktop", icons_dir / "traslado.png"]
        desktop_files = [p for p in shortcuts if p.suffix == ".desktop"]
        png_files = [p for p in shortcuts if p.suffix == ".png"]
        assert desktop_files and desktop_files[0].is_file(), f"Atalho .desktop ausente: {shortcuts}"
        assert png_files and png_files[0].is_file(), f"Ícone .png ausente: {shortcuts}"
        desktop_text = desktop_files[0].read_text(encoding="utf-8")
        assert "-I -m traslado.gui" in desktop_text, desktop_text
        report["shortcuts_verified"] = {
            "desktop_file": str(desktop_files[0]),
            "png_file": str(png_files[0]),
            "desktop_exec_contains_isolated_gui": "-I -m traslado.gui" in desktop_text,
        }

        lifecycle_rep = _verify_common_lifecycle(
            managed_root=managed_root,
            dist_dir=dist_dir,
            work_dir=work_dir,
            env=env,
            allowlist_path=ROOT / "ci/libs-permitidas-linux.txt",
            audit_output=audit_output,
            expect_degraded_ocr=True,
        )
        report.update(lifecycle_rep)

        # Desinstalar e verificar limpeza completa de raiz e atalhos
        py_exe = _managed_python(managed_root, current["release_id"])
        uninst_proc = subprocess.run(
            [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "uninstall", "--yes", "--root", str(managed_root)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        uninst_data = json.loads(uninst_proc.stdout.strip())
        assert not managed_root.exists(), f"Raiz gerenciada ainda existe após uninstall: {managed_root}"
        for sc in shortcuts:
            assert not sc.exists(), f"Atalho ainda existe após uninstall: {sc}"
        unexpected = [str(p) for p in Path(env["HOME"]).rglob("*") if p.is_file()]
        assert not unexpected, f"Arquivos fora da raiz/atalhos após uninstall: {unexpected}"
        report["uninstall"] = {
            "unexpected_home_files": unexpected,
            "result": uninst_data,
            "root_exists_after": managed_root.exists(),
            "shortcuts_exist_after": any(sc.exists() for sc in shortcuts),
        }
        return report


def _probe_windows_zone_identifier(dist_dir: Path, work_dir: Path) -> dict:
    """Marca ZIP, install.ps1 e INSTALAR.bat com Zone.Identifier (ZoneId=3) e mede a execução."""
    zone_dir = work_dir / "zone-test"
    zone_dir.mkdir(parents=True, exist_ok=True)
    zone_content = "[ZoneTransfer]\r\nZoneId=3\r\nReferrerUrl=https://github.com/rickfreoa/traslado/releases\r\n"

    ps1_copy = zone_dir / "install.ps1"
    shutil.copyfile(dist_dir / "install.ps1", ps1_copy)
    bat_copy = zone_dir / "INSTALAR.bat"
    shutil.copyfile(ROOT / "INSTALAR.bat", bat_copy)
    zips = list(dist_dir.glob("traslado-*-windows.zip"))
    zip_copy = zone_dir / zips[0].name if zips else None
    if zip_copy and zips:
        shutil.copyfile(zips[0], zip_copy)

    for target in [p for p in (ps1_copy, bat_copy, zip_copy) if p is not None]:
        ads_path = Path(str(target) + ":Zone.Identifier")
        ads_path.write_text(zone_content, encoding="ascii")

    ps_exe = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")

    # 1. Execução com política RemoteSigned sobre install.ps1 marcado com ZoneId=3
    proc_remote_signed = subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File", str(ps1_copy), "-Profile", "InvalidProfile"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    # 2. Execução com -ExecutionPolicy Bypass (como documentado para o comando de bootstrap e usado em INSTALAR.bat)
    proc_bypass = subprocess.run(
        [ps_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(ps1_copy), "-Profile", "InvalidProfile"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


    bat_proc = subprocess.run(["cmd.exe", "/d", "/c", str(bat_copy), "-Profile", "InvalidProfile"],
                              input="\n\n", capture_output=True, text=True, errors="replace", timeout=30)
    return {
        "zone_ads_applied": True,
        "bat_execution": {"returncode": bat_proc.returncode, "stdout": bat_proc.stdout, "stderr": bat_proc.stderr},
        "scope": "Execução não interativa; Profile inválido impede download. Sem avaliação de alertas visuais SmartScreen.",
        "ps1_zone_stream_present": Path(str(ps1_copy) + ":Zone.Identifier").is_file(),
        "remotesigned_policy": {
            "returncode": proc_remote_signed.returncode,
            "stderr_excerpt": (proc_remote_signed.stderr or "").strip()[:400],
            "note": "Exit não zero exige leitura de stderr; não implica SmartScreen nem MOTW.",
        },
        "bypass_policy": {
            "returncode": proc_bypass.returncode,
            "stdout_excerpt": (proc_bypass.stdout or "").strip()[:200],
        },
    }


def _probe_windows_servercore_container(dist_dir: Path) -> dict:
    """Tenta executar CLI em contêiner mcr.microsoft.com/windows/servercore:ltsc2022 ou registra limitação real."""
    image = "mcr.microsoft.com/windows/servercore:ltsc2022@sha256:a23b350061d76236e2c427e32175a2decfe3214200eee4ae9ee9cd9e98f26bf0"
    docker = shutil.which("docker")
    if not docker:
        return {"supported": False, "reason": "docker CLI ausente no runner"}
    try:
        info = subprocess.run([docker, "info", "--format", "{{.OSType}}"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return {"supported": False, "reason": "docker info sem resposta em 120 s (daemon indisponível no runner)"}
    os_type = (info.stdout or "").strip()
    if info.returncode != 0 or os_type != "windows":
        return {
            "supported": False,
            "docker_ostype": os_type,
            "reason": f"Docker daemon não está em modo Windows containers (OSType={os_type!r}, rc={info.returncode})",
            "stderr": (info.stderr or "").strip()[:300],
        }
    probe = subprocess.run(
        [docker, "run", "--rm", "-v", f"{dist_dir.resolve()}:C:/dist:ro",
         "-v", f"{ROOT / 'ci'}:C:/ci:ro",
         "-v", f"{ROOT / 'src/traslado/assets'}:C:/fixtures:ro",
         image, "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "C:/ci/e2e_servercore.ps1"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"Server Core E2E falhou: {probe.stdout}\n{probe.stderr}")
    return {
        "image": image,
        "supported": probe.returncode == 0,
        "docker_ostype": os_type,
        "returncode": probe.returncode,
        "stdout": (probe.stdout or "").strip()[:300],
        "stderr": (probe.stderr or "").strip()[:300],
    }


def run_windows_e2e(dist_dir: Path, audit_output: Path, ocr_cache: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="traslado-e2e-win-") as tmp:
        work_dir = Path(tmp)
        local_appdata = work_dir / "LocalAppData"
        managed_root = local_appdata / "Traslado"
        desktop_dir = work_dir / "Desktop"
        start_menu_dir = work_dir / "StartMenu"
        data_root = work_dir / "Documents" / "Traslado"
        for d in (local_appdata, desktop_dir, start_menu_dir, data_root):
            d.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        temp_dir = work_dir / "tmp"
        temp_dir.mkdir()
        env.update(TEMP=str(temp_dir), TMP=str(temp_dir), TMPDIR=str(temp_dir))
        profile_dir = work_dir / "profile"
        profile_dir.mkdir()
        env.update(USERPROFILE=str(profile_dir), HOME=str(profile_dir), APPDATA=str(profile_dir / "AppData/Roaming"))
        env["LOCALAPPDATA"] = str(local_appdata)
        env["TRASLADO_SHORTCUT_DESKTOP_DIR"] = str(desktop_dir)
        env["TRASLADO_SHORTCUT_START_MENU_DIR"] = str(start_menu_dir)
        env["TRASLADO_DATA_ROOT"] = str(data_root)
        env["TRASLADO_PROMOCAO_OFFLINE"] = "teste"
        if ocr_cache and ocr_cache.is_dir():
            env["TRASLADO_OCR_CACHE"] = str(ocr_cache.resolve())

        ps_exe = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        install_cmd = [
            ps_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str((dist_dir / "install.ps1").resolve()),
            "-FromDir",
            str(dist_dir.resolve()),
            "-Root",
            str(managed_root),
            "-Yes",
            "-AllowOfflinePromotion",
        ]
        inst_proc = subprocess.run(install_cmd, env=_sem_gh(env), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if inst_proc.returncode != 0:
            raise RuntimeError(f"install.ps1 falhou (rc={inst_proc.returncode}):\n{inst_proc.stdout}\n{inst_proc.stderr}")

        # Verificar .lnk (alvo pythonw.exe -I -m traslado.gui)
        desktop_lnk = desktop_dir / "Traslado.lnk"
        start_lnk = start_menu_dir / "Traslado.lnk"
        assert desktop_lnk.is_file(), f"Atalho Desktop ausente: {desktop_lnk}"
        assert start_lnk.is_file(), f"Atalho Menu Iniciar ausente: {start_lnk}"

        read_lnk_ps = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{desktop_lnk}'); "
            "@{TargetPath=$s.TargetPath; Arguments=$s.Arguments; IconLocation=$s.IconLocation} | ConvertTo-Json -Compress"
        )
        lnk_proc = subprocess.run(
            [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", read_lnk_ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        lnk_info = json.loads(lnk_proc.stdout.strip())
        assert lnk_info["TargetPath"].lower().endswith("pythonw.exe"), lnk_info
        assert lnk_info["Arguments"] == "-I -m traslado.gui", lnk_info

        report: dict = {
            "os": "windows",
            "runner": "windows-2022",
            "shortcuts_verified": {
                "desktop_lnk": str(desktop_lnk),
                "start_menu_lnk": str(start_lnk),
                "target_path": lnk_info["TargetPath"],
                "arguments": lnk_info["Arguments"],
            },
        }

        lifecycle_rep = _verify_common_lifecycle(
            managed_root=managed_root,
            dist_dir=dist_dir,
            work_dir=work_dir,
            env=env,
            allowlist_path=ROOT / "ci/libs-permitidas-windows.txt",
            audit_output=audit_output,
            expect_degraded_ocr=False,
        )
        report.update(lifecycle_rep)

        report["quarantine_zone_identifier"] = _probe_windows_zone_identifier(dist_dir, work_dir)
        report["servercore_container_probe"] = _probe_windows_servercore_container(dist_dir)

        # Desinstalar e conferir remoção
        current = json.loads((managed_root / "current.json").read_text(encoding="utf-8"))
        py_exe = _managed_python(managed_root, current["release_id"])
        sentinela = data_root / "sentinela-dados-usuario.txt"
        sentinela.write_text("dado do usuario", encoding="utf-8")
        uninst_proc = subprocess.run(
            [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "uninstall", "--yes", "--root", str(managed_root)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        deadline = time.monotonic() + 30
        while managed_root.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not managed_root.exists(), f"Raiz ainda existe após uninstall: {managed_root}"
        assert not desktop_lnk.exists() and not start_lnk.exists(), "Atalhos .lnk ainda existem após uninstall"
        unexpected = [str(p) for p in profile_dir.rglob("*") if p.is_file()]
        # The redirected LOCALAPPDATA must also be empty of files after uninstall.
        if os.name == "nt":
            unexpected += [str(p) for p in local_appdata.rglob("*") if p.is_file()]
        assert not unexpected, f"Arquivos fora da raiz/atalhos após uninstall: {unexpected}"
        assert sentinela.read_text(encoding="utf-8") == "dado do usuario", "data_root não preservado no uninstall"
        report["uninstall"] = {
            "unexpected_profile_files": unexpected,
            "result": json.loads(uninst_proc.stdout.strip()),
            "root_exists_after": managed_root.exists(),
            "shortcuts_exist_after": desktop_lnk.exists() or start_lnk.exists(),
            "data_root_preserved": sentinela.read_text(encoding="utf-8") == "dado do usuario",
        }
        return report


def _probe_macos_quarantine(app_bundle: Path, work_dir: Path) -> dict:
    """Marca instalar.command e Traslado.app com com.apple.quarantine e mede o resultado."""
    q_val = "0083;6623b000;Safari;00000000-0000-0000-0000-000000000000"
    cmd_copy = work_dir / "instalar.command"
    shutil.copyfile(ROOT / "instalar.command", cmd_copy)
    cmd_copy.chmod(0o755)

    subprocess.run(["xattr", "-w", "com.apple.quarantine", q_val, str(cmd_copy)], check=True)
    subprocess.run(["xattr", "-r", "-w", "com.apple.quarantine", q_val, str(app_bundle)], check=True)

    xattr_cmd = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(cmd_copy)], capture_output=True, text=True, check=True)
    xattr_app = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(app_bundle)], capture_output=True, text=True, check=True)

    spctl_proc = subprocess.run(
        ["spctl", "--assess", "--type", "execute", "-vv", str(app_bundle)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "quarantine_xattr_applied": True,
        "instalar_command_xattr": xattr_cmd.stdout.strip(),
        "app_bundle_xattr": xattr_app.stdout.strip(),
        "spctl_assess": {
            "returncode": spctl_proc.returncode,
            "stderr_excerpt": (spctl_proc.stderr or spctl_proc.stdout or "").strip()[:400],
            "assessment_rejected": spctl_proc.returncode != 0,
        },
    }


def run_macos_e2e(dist_dir: Path, audit_output: Path) -> dict:
    if platform.machine() != "arm64":
        raise RuntimeError(f"macos-14 deve fornecer arm64; observado: {platform.machine()}")
    with tempfile.TemporaryDirectory(prefix="traslado-e2e-macos-") as tmp:
        work_dir = Path(tmp)
        managed_root = work_dir / "Library" / "Application Support" / "Traslado"
        apps_dir = work_dir / "Applications"
        data_root = work_dir / "Documents" / "Traslado"
        for d in (managed_root.parent, apps_dir, data_root):
            d.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        temp_dir = work_dir / "tmp"
        temp_dir.mkdir()
        env.update(TEMP=str(temp_dir), TMP=str(temp_dir), TMPDIR=str(temp_dir))
        profile_dir = work_dir / "profile"
        profile_dir.mkdir()
        env["HOME"] = str(profile_dir)
        env["TRASLADO_SHORTCUT_APPLICATIONS_DIR"] = str(apps_dir)
        env["TRASLADO_DATA_ROOT"] = str(data_root)
        env["TRASLADO_PROMOCAO_OFFLINE"] = "teste"

        inst_proc = subprocess.run(
            ["sh", str(dist_dir / "install.sh"), "--from-dir", str(dist_dir), "--root", str(managed_root), "--yes",
             "--allow-offline-promotion"],
            env=_sem_gh(env),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if inst_proc.returncode != 0:
            raise RuntimeError(f"install.sh no macOS falhou (rc={inst_proc.returncode}):\n{inst_proc.stdout}\n{inst_proc.stderr}")

        app_bundle = apps_dir / "Traslado.app"
        info_plist = app_bundle / "Contents" / "Info.plist"
        launcher = app_bundle / "Contents" / "MacOS" / "Traslado"
        icon_png = app_bundle / "Contents" / "Resources" / "traslado.png"
        assert info_plist.is_file(), f"Info.plist ausente em {app_bundle}"
        plist = plistlib.loads(info_plist.read_bytes())
        assert plist.get("CFBundleExecutable") == "Traslado", plist
        assert launcher.is_file() and os.access(launcher, os.X_OK), f"Launcher não executável em {app_bundle}"
        assert icon_png.is_file(), f"Ícone ausente em {app_bundle}"
        launcher_txt = launcher.read_text(encoding="utf-8")
        assert "-I -m traslado.gui" in launcher_txt, launcher_txt

        report: dict = {
            "os": "macos",
            "runner": "macos-14",
            "autonomy_limit_note": "Em macOS a autonomia é provada pela auditoria de bibliotecas carregadas (otool -L, DYLD_PRINT_LIBRARIES e vmmap), não por imagem virgem do sistema operacional.",
            "shortcuts_verified": {
                "app_bundle": str(app_bundle),
                "info_plist": str(info_plist),
                "launcher": str(launcher),
                "launcher_exec_isolated_gui": "-I -m traslado.gui" in launcher_txt,
            },
        }

        lifecycle_rep = _verify_common_lifecycle(
            managed_root=managed_root,
            dist_dir=dist_dir,
            work_dir=work_dir,
            env=env,
            allowlist_path=ROOT / "ci/libs-permitidas-macos.txt",
            audit_output=audit_output,
            expect_degraded_ocr=True,
        )
        report.update(lifecycle_rep)
        report["quarantine_xattr"] = _probe_macos_quarantine(app_bundle, work_dir)

        current = json.loads((managed_root / "current.json").read_text(encoding="utf-8"))
        py_exe = _managed_python(managed_root, current["release_id"])
        sentinela = data_root / "sentinela-dados-usuario.txt"
        sentinela.write_text("dado do usuario", encoding="utf-8")
        uninst_proc = subprocess.run(
            [str(py_exe), "-I", "-X", "utf8", "-m", "traslado", "uninstall", "--yes", "--root", str(managed_root)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        deadline = time.monotonic() + 30
        while managed_root.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not managed_root.exists(), f"Raiz ainda existe após uninstall: {managed_root}"
        assert not app_bundle.exists(), f"Traslado.app ainda existe após uninstall: {app_bundle}"
        unexpected = [str(p) for p in profile_dir.rglob("*") if p.is_file()]
        # The redirected LOCALAPPDATA must also be empty of files after uninstall.
        if os.name == "nt":
            unexpected += [str(p) for p in local_appdata.rglob("*") if p.is_file()]
        assert not unexpected, f"Arquivos fora da raiz/atalhos após uninstall: {unexpected}"
        assert sentinela.read_text(encoding="utf-8") == "dado do usuario", "data_root não preservado no uninstall"
        report["uninstall"] = {
            "unexpected_profile_files": unexpected,
            "result": json.loads(uninst_proc.stdout.strip()),
            "root_exists_after": managed_root.exists(),
            "app_bundle_exists_after": app_bundle.exists(),
            "data_root_preserved": sentinela.read_text(encoding="utf-8") == "dado do usuario",
        }
        return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=("linux-container", "windows", "macos"), required=True)
    ap.add_argument("--dist", type=Path, required=True, help="Diretório dist/ com os artefatos construídos")
    ap.add_argument("--root", type=Path, default=None, help="Raiz gerenciada (usada em linux-container)")
    ap.add_argument("--pre-inventory", type=Path, default=None, help="JSON do inventário pré-instalação do contêiner")
    ap.add_argument("--ocr-cache", type=Path, default=None, help="Cache de downloads OCR no Windows")
    ap.add_argument("--saida", type=Path, required=True, help="Arquivo JSON de saída do relatório E2E")
    ap.add_argument("--audit-saida", type=Path, required=True, help="Arquivo JSON de saída da auditoria de bibliotecas")
    args = ap.parse_args(argv)

    try:
        if args.mode == "linux-container":
            if args.root is None:
                raise ValueError("--root é obrigatório em --mode linux-container")
            rep = run_linux_container_post_install(args.root, args.dist, args.audit_saida, args.pre_inventory)
        elif args.mode == "windows":
            rep = run_windows_e2e(args.dist, args.audit_saida, args.ocr_cache)
        else:
            rep = run_macos_e2e(args.dist, args.audit_saida)
    
    except Exception as exc:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text(json.dumps({"success": False, "mode": args.mode,
            "error": str(exc), "traceback": traceback.format_exc()}, indent=2) + "\n", encoding="utf-8")
        raise

    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"E2E ({args.mode}) concluído com sucesso: {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
