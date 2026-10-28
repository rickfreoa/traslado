# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import hashlib
import codecs
import os
from .errors import ConversionError, ConversionTimeout

from .pathguard import unsafe_link
from pathlib import Path, PurePosixPath
import stat
import subprocess
import time
import zipfile
from typing import Callable

from .model import Options


class Cancelled(ConversionError):
    def __init__(self, message):
        super().__init__("cancelled", message)


class SnapshotLock:
    """Hold the Windows snapshot open denying writes/deletion during parsing.

    Other platforms retain a read handle, without a mandatory filesystem lock.
    This does not isolate the output directory or third-party processes.
    """
    def __init__(self, path: Path):
        self.handle = None
        self.stream = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            self.kernel.CreateFileW.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            handle = self.kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = handle
        else:
            self.stream = path.open("rb")

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def is_link(path: Path) -> bool:
    return unsafe_link(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check_archive(path: Path, options: Options) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 20000 or sum(x.file_size for x in infos) > options.max_archive_bytes:
            raise ConversionError('resource_limit', "Arquivo Office excede o limite de descompressão")
        names: set[str] = set()
        for info in infos:
            name = info.filename.replace("\\", "/")
            if name in names:
                raise ConversionError('malformed', "Membro ZIP duplicado")
            names.add(name)
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or ":" in name:
                raise ConversionError('malformed', "Caminho ZIP inseguro")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ConversionError('malformed', "Link ZIP não permitido")
            if info.file_size > 10 * 1024 * 1024 and info.file_size / max(info.compress_size, 1) > 500:
                raise ConversionError('resource_limit', "Taxa de compressão Office excessiva")
            if name.lower().endswith((".xml", ".rels")):
                with archive.open(info) as stream:
                    first = stream.read(64 * 1024)
                    encoding = "utf-8-sig"
                    if first.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
                        encoding = "utf-32"
                    elif first.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                        encoding = "utf-16"
                    elif b"\0" in first:
                        raise ConversionError('malformed', "XML sem BOM ou codificação não suportada")
                    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
                    tail = ""
                    chunk = first
                    while chunk:
                        try:
                            combined = (tail + decoder.decode(chunk)).lower()
                        except UnicodeError as exc:
                            raise ConversionError('malformed', "Codificação XML não suportada") from exc
                        if "<!doctype" in combined or "<!entity" in combined:
                            raise ConversionError('malformed', "DTD ou entidades XML não permitidos")
                        tail = combined[-16:]
                        chunk = stream.read(64 * 1024)
                    decoder.decode(b"", final=True)


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        if proc.poll() is not None:
            return  # the Windows Job Object owns descendants on normal exit
        taskkill = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        # The session/group may still have descendants after its leader exits.
        # Only explicitly shared children leave group cleanup to the supervisor.
        try:
            if getattr(proc, "_shared_session", False):
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run_bounded(argv: list[str], cwd: Path, timeout: int, log: Path,
                cancel: Callable[[], bool] | None = None, env: dict | None = None,
                max_log_bytes: int = 4 * 1024 * 1024,
                max_memory_bytes: int = 2 * 1024 * 1024 * 1024,
                budget_root: Path | None = None, max_directory_bytes: int | None = None,
                on_poll: Callable[[], None] | None = None,
                share_session: bool = False) -> int:
    kwargs = ({"creationflags": subprocess.CREATE_NO_WINDOW | 0x4} if os.name == "nt"
              else {"start_new_session": not share_session})
    job = None
    if os.name == "nt":
        from .windows_job import WindowsJob
        job = WindowsJob(max_memory_bytes)
    started = time.monotonic()
    with log.open("wb") as stream:
        proc = None
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stream, stderr=stream, shell=False, **kwargs)
            proc._shared_session = share_session
            if job:
                job.assign_and_resume(proc)
            while proc.poll() is None:
                if on_poll:
                    on_poll()
                if cancel and cancel():
                    raise Cancelled("Conversão cancelada")
                if time.monotonic() - started > timeout:
                    raise ConversionTimeout("Tempo limite excedido")
                if log.stat().st_size > max_log_bytes:
                    raise ConversionError('resource_limit', "Log excede o limite")
                if budget_root and max_directory_bytes:
                    total = 0
                    for path in budget_root.rglob("*"):
                        try:
                            if path.is_file():
                                total += path.stat().st_size
                        except FileNotFoundError:
                            continue
                        if total > max_directory_bytes:
                            raise ConversionError('resource_limit', "Arquivos temporários excedem limite de saída")
                time.sleep(0.05)
            if log.stat().st_size > max_log_bytes:
                raise ConversionError('resource_limit', "Log excede o limite")
            return proc.returncode
        finally:
            if job:
                job.close()  # kills descendants even if the original process exited normally
            if proc:
                kill_tree(proc)


def safe_child(root: Path, name: str) -> Path:
    path = root / name
    if path.resolve().parent != root.resolve() or path.is_symlink():
        raise ConversionError('malformed', "Saída insegura")
    return path
