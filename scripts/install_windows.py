"""Per-user transactional installer. Never executes a previous environment."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

PRODUCT = "freoa-md-converter"
VERSION = "1.0.0"
LOG_DIRECTORY = None
LANGUAGES = ("por", "eng", "spa", "ita", "fra", "deu")
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def no_links(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Link/junction não permitido: " + str(item))
        try:
            info = item.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if attributes & 0x400 and (getattr(info, "st_reparse_tag", 0) & 0xFFFF0FFF) != 0x9000001A:
            raise ValueError("Reparse point não permitido: " + str(item))
    return path.resolve()


def inside(path, root):
    root = no_links(root)
    path = no_links(path)
    if root not in path.parents:
        raise ValueError("Operação fora da instalação: " + str(path))
    return path


def read_json(path):
    path = no_links(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Registro excessivo")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    path = no_links(path)
    temp = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def run(argv, *, capture=False, timeout=1200, env=None):
    print("Etapa: " + Path(str(argv[0])).name, flush=True)
    job = None
    process = None
    try:
        if os.name == "nt":
            # Reuse the reviewed process boundary from this authenticated payload;
            # no installed/previous package is imported by the installer.
            package = Path(__file__).resolve().parents[1]
            helper = package / "src/freoa_v6/windows_job.py"
            manifest = read_json(package / "INSTALL-PAYLOAD.json")
            entries = [x for x in manifest["files"] if x["path"] == "src/freoa_v6/windows_job.py"]
            data = no_links(helper).read_bytes()
            if len(entries) != 1 or hashlib.sha256(data).hexdigest() != entries[0]["sha256"]:
                raise ValueError("Supervisor de instalação mudou")
            spec = importlib.util.spec_from_file_location("freoa_install_job", helper)
            module = importlib.util.module_from_spec(spec)
            exec(compile(data, str(helper), "exec"), module.__dict__)
            job = module.WindowsJob()
        with tempfile.TemporaryFile() as out_stream, tempfile.TemporaryFile() as err_stream:
            try:
                process = subprocess.Popen([str(x) for x in argv], env=env,
                    stdout=out_stream, stderr=err_stream, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW | 0x4 if job else 0)
                if job:
                    job.assign_and_resume(process)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if os.fstat(out_stream.fileno()).st_size + os.fstat(err_stream.fileno()).st_size > 64 * 1024 * 1024:
                        raise ValueError("Log da etapa excede 64 MiB")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(process.args, timeout)
                    try:
                        process.wait(timeout=min(0.25, remaining))
                    except subprocess.TimeoutExpired:
                        continue
                if os.fstat(out_stream.fileno()).st_size + os.fstat(err_stream.fileno()).st_size > 64 * 1024 * 1024:
                    raise ValueError("Log da etapa excede 64 MiB")
            finally:
                if job:
                    job.close()
                    job = None
                if process and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                out_stream.seek(0); err_stream.seek(0)
                # Persist each stage independently, even when its timeout aborts installation.
                log = None
                if LOG_DIRECTORY is not None:
                    log = no_links(LOG_DIRECTORY / ("stage-" + uuid.uuid4().hex + ".log"))
                    with log.open("xb") as destination:
                        destination.write(("Etapa: " + Path(str(argv[0])).name + "\n").encode("utf-8"))
                        out_stream.seek(max(0, os.fstat(out_stream.fileno()).st_size - 8 * 1024 * 1024))
                        shutil.copyfileobj(out_stream, destination)
                        destination.write(b"\n--- stderr ---\n")
                        err_stream.seek(max(0, os.fstat(err_stream.fileno()).st_size - 8 * 1024 * 1024))
                        shutil.copyfileobj(err_stream, destination)
                    print("Registro: " + str(log), flush=True)
                out_stream.seek(0); err_stream.seek(0)
                if not capture:
                    out_stream.seek(max(0, os.fstat(out_stream.fileno()).st_size - 8 * 1024 * 1024))
                stdout = out_stream.read(8 * 1024 * 1024 + 1)
                err_stream.seek(max(0, os.fstat(err_stream.fileno()).st_size - 8 * 1024 * 1024))
                stderr = err_stream.read(8 * 1024 * 1024)
                if capture and len(stdout) > 8 * 1024 * 1024:
                    raise ValueError("Resposta capturada da etapa excede 8 MiB")
            if process.returncode:
                details = (stderr or stdout).decode("utf-8", errors="replace")[-6000:]
                print("Falha em " + Path(str(argv[0])).name + ":\n" + details, file=sys.stderr, flush=True)
                raise subprocess.CalledProcessError(process.returncode, process.args, output=stdout, stderr=stderr)
            return stdout.decode("utf-8-sig", errors="strict") if capture else None
    finally:
        if job:
            job.close()
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def clean_environment():
    environment = os.environ.copy()
    for name in list(environment):
        if name.startswith("PYTHON") or name.startswith("PIP_"):
            environment.pop(name)
    environment.update(PIP_CONFIG_FILE="NUL", PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8",
                       PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return environment


def copy_payload(package, release):
    manifest = read_json(package / "INSTALL-PAYLOAD.json")
    if manifest.get("version") != VERSION or manifest.get("product") != PRODUCT:
        raise ValueError("Manifesto de instalação incompatível")
    seen = set()
    for item in manifest["files"]:
        name = item["path"]
        if name in seen or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or ":" in name or "\\" in name:
            raise ValueError("Caminho de payload inválido")
        seen.add(name)
        source = inside(package / name, package)
        if not source.is_file() or digest(source) != item["sha256"]:
            raise ValueError("Payload mudou: " + name)
        target = inside(release / name, release)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if digest(target) != item["sha256"]:
            raise ValueError("Cópia não autenticada: " + name)
    if not any(x.startswith("dist/") and x.endswith(".whl") for x in seen):
        raise ValueError("Wheel próprio ausente")
    return manifest


def release_path(root, record):
    if record.get("product") != PRODUCT or record.get("install_id") != read_json(root / ".freoa-installation.json")["install_id"]:
        raise ValueError("Registro não pertence à instalação")
    name = record.get("release_id", "")
    if not re.fullmatch(r"[0-9A-Za-z.-]+_[0-9a-f]{32}", name):
        raise ValueError("Identificador de versão inválido")
    release = inside(root / "releases" / name, root)
    marker = read_json(release / ".freoa-release.json")
    if marker.get("install_id") != record["install_id"] or marker.get("release_id") != name or marker.get("product") != PRODUCT:
        raise ValueError("Versão anterior não reconhecida")
    return release, marker


def inventory(folder):
    items = []
    for file in sorted(folder.rglob("*")):
        inside(file, folder)
        if file.is_file() and file != folder / ".freoa-release.json":
            items.append({"path": file.relative_to(folder).as_posix(), "sha256": digest(file)})
    return items


def remove_managed_release(root, record, data_root):
    folder, marker = release_path(root, record)
    known = {item["path"]: item["sha256"] for item in marker.get("files", [])}
    backup = data_root / "preservados-na-atualizacao" / folder.name
    preserved = []
    for file in sorted(folder.rglob("*")):
        inside(file, folder)
        if not file.is_file() or file == folder / ".freoa-release.json":
            continue
        name = file.relative_to(folder).as_posix()
        if known.get(name) != digest(file):
            target = inside(backup / name, data_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
            if digest(file) != digest(target):
                raise ValueError("Falha ao preservar arquivo da versão anterior")
            preserved.append(str(target))
    # Fully resolved target and every tree entry checked before recursive delete.
    inside(folder, root / "releases")
    shutil.rmtree(folder)
    return preserved


def shortcut(action, path, script, *, target="", data_root=""):
    argv = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
            "-Action", action, "-ShortcutPath", str(path)]
    if action == "Create":
        argv.extend(["-Target", str(target), "-WorkingDirectory", str(data_root)])
    output = run(argv, capture=True, timeout=30, env=clean_environment())
    return json.loads(output)


def assert_runtime_idle(folder, package):
    active = json.loads(run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        package / "scripts/installation_processes.ps1", "-ReleaseRoot", folder], capture=True, timeout=30,
        env=clean_environment()))
    if active:
        raise ValueError("Feche a janela e conversões da versão anterior e execute o instalador novamente; nenhum documento foi removido")


def recognize_legacy(folder, package):
    folder = no_links(folder)
    metadata = folder / "pyproject.toml"
    if not metadata.is_file() or metadata.stat().st_size > 65536:
        raise ValueError("Versão antiga não reconhecida")
    import tomllib
    project = tomllib.loads(metadata.read_text(encoding="utf-8-sig"))["project"]
    if project.get("name") != PRODUCT or not re.fullmatch(r"(?:5\.5\.0|6\.0\.0rc[12])", project.get("version", "")):
        raise ValueError("Versão antiga fora do catálogo de migração")
    catalog = read_json(package / "LEGACY-CATALOG.json")
    files = catalog.get(project["version"])
    if not files:
        raise ValueError("Catálogo da versão antiga ausente")
    # Confirm several effective application bytes, without running old Python.
    proofs = [item for item in files if item["path"].startswith("src/") and item["path"].endswith(".py")]
    matched = sum((folder / item["path"]).is_file() and digest(inside(folder / item["path"], folder)) == item["sha256"] for item in proofs)
    if matched < 3:
        raise ValueError("Código da instalação antiga não autenticado")
    venv = folder / ".venv"
    if venv.exists():
        inside(venv, folder)
        markers = list((venv / "Lib/site-packages").glob("freoa_md_converter-*.dist-info/METADATA"))
        if not markers or not any("Name: freoa-md-converter" in inside(x, folder).read_text(encoding="utf-8") for x in markers):
            raise ValueError("Ambiente antigo não pertence ao conversor")
    return folder, project["version"], files


def remove_legacy(record, data_root):
    folder, version, files = record
    preserved, removed = [], []
    backup = data_root / "preservados-na-atualizacao" / ("legacy-" + version + "-" + uuid.uuid4().hex)
    for item in files:
        name = item["path"]
        # User configs/examples/manuals/results are never application-delete targets.
        if not (name.startswith("src/") and name.endswith(".py") or Path(name).suffix.lower() in {".bat", ".ps1"} or name == "pyproject.toml"):
            continue
        file = inside(folder / name, folder)
        if not file.is_file():
            continue
        if digest(file) != item["sha256"]:
            preserved.append(str(file))
            continue
        target = inside(backup / name, data_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        if digest(target) != item["sha256"]:
            raise ValueError("Backup da aplicação antiga falhou")
        file.unlink()
        removed.append(str(file))
    venv = folder / ".venv"
    if venv.exists():
        inside(venv, folder)
        for file in venv.rglob("*"):
            inside(file, venv)
        # Preserve any data manually placed in the old runtime before removing it.
        runtime_backup = inside(backup / "old-runtime", data_root)
        shutil.copytree(venv, runtime_backup)
        for file in venv.rglob("*"):
            if file.is_file() and digest(file) != digest(runtime_backup / file.relative_to(venv)):
                raise ValueError("Backup do runtime antigo falhou")
        shutil.rmtree(inside(venv, folder))
        removed.append(str(venv))
    return {"version": version, "root": str(folder), "removed": removed, "preserved_or_modified": preserved,
            "backup": str(backup), "user_files": "kept in original folders"}


def install(args):
    global LOG_DIRECTORY
    if os.name != "nt":
        raise ValueError("Instalador destinado ao Windows")
    package = no_links(args.package)
    root = no_links(args.root)
    data_root = no_links(args.data_root)
    desktop = no_links(args.shortcut_directory)
    forbidden = {Path(root.anchor), Path.home().resolve(), Path(os.environ["LOCALAPPDATA"]).resolve()}
    if root in forbidden or root == package or package in root.parents or root in package.parents or root == data_root or root in data_root.parents or data_root in root.parents or data_root == package or package in data_root.parents or data_root in package.parents:
        raise ValueError("Escolha pastas separadas para aplicativo, pacote e documentos")
    root.mkdir(parents=True, exist_ok=True)
    marker_path = root / ".freoa-installation.json"
    if marker_path.exists():
        marker = read_json(marker_path)
        if marker.get("product") != PRODUCT or marker.get("schema_version") != 1 or not re.fullmatch(r"[0-9a-f]{32}", marker.get("install_id", "")):
            raise ValueError("Pasta existente não pertence ao conversor")
    else:
        if any(root.iterdir()):
            raise ValueError("Pasta existente desconhecida; seus arquivos foram preservados")
        marker = {"product": PRODUCT, "schema_version": 1, "install_id": uuid.uuid4().hex}
        with marker_path.open("x", encoding="utf-8") as stream:
            json.dump(marker, stream)
    lock = root / ".installation-lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()))
    previous_log_directory = LOG_DIRECTORY
    release = None
    committed = False
    current_path = root / "current.json"
    previous_bytes = None
    prior = None
    temporary_shortcut = None
    warnings = []
    try:
        LOG_DIRECTORY = no_links(root / "installation-logs")
        LOG_DIRECTORY.mkdir(exist_ok=True)
        if current_path.exists():
            prior = read_json(current_path)
            previous_bytes = current_path.read_bytes()
        if previous_bytes is not None:
            previous_folder, _ = release_path(root, prior)
            assert_runtime_idle(previous_folder, package)
        shortcut_path = desktop / "Freoa Markdown Converter.lnk"
        helper = package / "scripts/shortcut_windows.ps1"
        old_shortcut = shortcut("Inspect", shortcut_path, helper) if shortcut_path.exists() else None
        if old_shortcut and not old_shortcut.get("recognized"):
            raise ValueError("Atalho homônimo pertence a outro aplicativo; não será substituído")
        if old_shortcut and prior:
            prior_folder, _ = release_path(root, prior)
            expected = prior_folder / ".venv/Scripts/pythonw.exe"
            if no_links(old_shortcut["target"]) != expected.resolve():
                raise ValueError("Atalho aponta outra instalação; use a pasta de instalação correspondente")
        legacy_roots = []
        if args.legacy_root:
            legacy_roots.append(Path(args.legacy_root))
        elif old_shortcut and not prior:
            legacy_roots.append(Path(old_shortcut["target"]).parent.parent.parent)
        if (package / ".venv").exists():
            legacy_roots.append(package)
        legacy = []
        for old in dict.fromkeys(legacy_roots):
            old = no_links(old)
            if old == root or root in old.parents or old in root.parents or old == data_root or old in data_root.parents or data_root in old.parents:
                raise ValueError("Instalação antiga se sobrepõe a dados ou nova instalação")
            recognized = recognize_legacy(old, package)
            assert_runtime_idle(old, package)
            legacy.append(recognized)
        release_id = VERSION + "_" + uuid.uuid4().hex
        releases = root / "releases"
        no_links(releases).mkdir(exist_ok=True)
        release = releases / release_id
        release.mkdir()  # exclusive new path: never reuse a prior environment
        payload = copy_payload(package, release)
        print("Instalando " + args.profile + " em pasta nova; preparando bibliotecas...", flush=True)
        environment = clean_environment()
        venv = release / ".venv"
        run([sys.executable, "-I", "-m", "venv", venv], env=environment)
        python = venv / "Scripts/python.exe"
        if args.profile == "Full":
            run([python, "-I", "-m", "pip", "install", "--no-input", "--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--require-hashes", "-r", release / "requirements-full.hashes.lock"], env=environment)
        wheels = list((release / "dist").glob(f"freoa_md_converter-{VERSION}-*.whl"))
        if len(wheels) != 1:
            raise ValueError("Wheel próprio ambíguo")
        run([python, "-I", "-m", "pip", "install", "--no-input", "--no-index", "--no-deps", wheels[0]], env=environment)
        use_ocr = args.profile == "Full" and not args.skip_ocr
        if use_ocr:
            command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", release / "scripts/bootstrap_ocr.ps1",
                       "-Destination", release / "ocr", "-CacheDirectory", package / "ocr-cache", "-Languages", ",".join(args.languages)]
            run(command, env=environment)
        data_root.mkdir(parents=True, exist_ok=True)
        atomic_json(release / "freoa-runtime.json", {"schema_version": 1, "languages": args.languages,
            "data_root": str(data_root), "profile": args.profile, "ocr_installed": use_ocr})
        doctor_file = release / "installation-doctor.json"
        doctor = json.loads(run([python, "-I", "-m", "freoa_v6", "doctor"], capture=True, env=environment))
        if args.profile == "Full" and not all(doctor["capabilities"][x]["available"] for x in ["fitz", "PIL", "docx", "openpyxl", "pptx"]):
            raise ValueError("Bibliotecas Full não disponíveis")
        if use_ocr and not set(args.languages) <= set(doctor["languages"]):
            raise ValueError("Idiomas OCR não disponíveis")
        atomic_json(doctor_file, doctor)
        run([python, "-I", "-m", "pip", "check"], env=environment)
        record = {"schema_version": 1, "product": PRODUCT, "install_id": marker["install_id"],
                  "release_id": release_id, "version": VERSION, "profile": args.profile,
                  "data_root": str(data_root), "languages": args.languages, "ocr_installed": use_ocr}
        atomic_json(release / ".freoa-release.json", dict(record, files=inventory(release)))
        if not args.no_shortcut:
            desktop.mkdir(parents=True, exist_ok=True)
            temporary_shortcut = desktop / (".Freoa-" + uuid.uuid4().hex + ".lnk")
            shortcut("Create", temporary_shortcut, helper, target=venv / "Scripts/pythonw.exe", data_root=data_root)
        atomic_json(current_path, record)
        try:
            if temporary_shortcut:
                os.replace(temporary_shortcut, shortcut_path)
                temporary_shortcut = None
            elif old_shortcut and args.no_shortcut:
                shutil.copyfile(shortcut_path, data_root / ("previous-shortcut-" + uuid.uuid4().hex + ".lnk"))
                shortcut_path.unlink()
        except Exception:
            if previous_bytes is None:
                current_path.unlink(missing_ok=True)
            else:
                restore = current_path.with_name(".restore-" + uuid.uuid4().hex)
                restore.write_bytes(previous_bytes)
                os.replace(restore, current_path)
            raise
        committed = True
        retained = []
        if prior:
            try:
                retained.extend(remove_managed_release(root, prior, data_root))
            except (OSError, ValueError) as exc:
                warnings.append("Nova versão instalada; remoção anterior pendente: " + str(exc))
        migrations = []
        for old in legacy:
            try:
                migrations.append(remove_legacy(old, data_root))
            except (OSError, ValueError) as exc:
                warnings.append("Nova versão instalada; migração antiga pendente: " + str(exc))
        result = dict(record, status="installed" if not warnings else "installed_with_warnings",
                      root=str(root), shortcut=str(shortcut_path) if not args.no_shortcut else None,
                      preserved_files=retained, legacy_migrations=migrations, warnings=warnings)
        if args.launch:
            try:
                subprocess.Popen([str(venv / "Scripts/pythonw.exe"), "-I", "-m", "freoa_v6", "gui"],
                                 cwd=data_root, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as exc:
                warnings.append("Instalação concluída; abra pelo atalho: " + str(exc))
        result["status"] = "installed_with_warnings" if warnings else "installed"
        try:
            atomic_json(root / "last-installation.json", result)
        except (OSError, ValueError) as exc:
            warnings.append("Instalação concluída; não foi possível salvar recibo: " + str(exc))
            result["status"] = "installed_with_warnings"
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return result
    finally:
        try:
            if temporary_shortcut:
                temporary_shortcut.unlink(missing_ok=True)
            if not committed and release and release.exists():
                # New release reserved exclusively by this invocation; validate
                # tree before removal. A guarded refusal still releases the lock.
                inside(release, root / "releases")
                for file in release.rglob("*"):
                    inside(file, release)
                shutil.rmtree(release)
        finally:
            LOG_DIRECTORY = previous_log_directory
            lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--shortcut-directory", type=Path, required=True)
    parser.add_argument("--profile", choices=["Full", "Core"], default="Full")
    parser.add_argument("--languages", default=",".join(LANGUAGES))
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--no-shortcut", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--legacy-root", type=Path)
    args = parser.parse_args()
    args.languages = args.languages.replace("+", ",").split(",")
    if not args.languages or len(set(args.languages)) != len(args.languages) or not set(args.languages) <= set(LANGUAGES):
        parser.error("Selecione idiomas por,eng,spa,ita,fra,deu sem duplicatas")
    try:
        install(args)
    except subprocess.CalledProcessError as exc:
        print("Instalação interrompida na etapa acima (código " + str(exc.returncode) + ").", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
