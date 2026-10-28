"""Portable managed installations and desktop integration (stdlib only)."""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
import os
from pathlib import Path
import platform as host
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


def managed_root(*, platform=None, env=None) -> Path:
    platform, env = platform or sys.platform, os.environ if env is None else env
    home = Path(env.get('HOME') or env.get('USERPROFILE') or Path.home())
    if platform == 'win32':
        return Path(env.get('LOCALAPPDATA') or home / 'AppData/Local') / 'Traslado'
    if platform == 'darwin':
        return home / 'Library/Application Support/Traslado'
    return Path(env.get('XDG_DATA_HOME') or home / '.local/share') / 'traslado'


def legacy_roots(*, platform=None, env=None) -> list[Path]:
    platform = platform or sys.platform
    return [managed_root(platform=platform, env=env).parent / 'Freoa/MarkdownConverter'] if platform == 'win32' else []


def _release(root, release_id):
    if not isinstance(release_id, str) or not release_id or release_id in {'.', '..'} or any(c in release_id for c in '/\\:\x00\r\n'):
        raise ValueError('release_id inválido')
    return Path(root) / 'releases' / release_id


def release_python(root, release_id, *, platform=None) -> Path:
    return _release(root, release_id) / ('.venv/Scripts/python.exe' if (platform or sys.platform) == 'win32' else '.venv/bin/python')


def release_pythonw(root, release_id, *, platform=None) -> Path:
    python = release_python(root, release_id, platform=platform)
    return python.with_name('pythonw.exe') if (platform or sys.platform) == 'win32' else python


def _read(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('Manifesto deve ser um objeto')
    return data


def _optional(path):
    try:
        return _read(path)
    except (OSError, ValueError):
        return None


def read_current(root):
    return _optional(Path(root) / 'current.json')


def read_candidate(root):
    return _optional(Path(root) / 'candidate.json')


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_candidate(root, data):
    if not isinstance(data, dict):
        raise ValueError('Manifesto deve ser um objeto')
    _release(root, data.get('release_id'))
    _atomic(Path(root) / 'candidate.json', data)


@contextmanager
def _promotion_lock(root):
    lock = root / 'state/promote.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            stream = lock.open('x', encoding='utf-8')
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < 600:
                raise ValueError('Promoção/rollback em andamento: state/promote.lock')
            # Serialize stale takeovers too: a second contender must recheck
            # the new lock, rather than unlink one just acquired by its peer.
            guard = lock.with_name('promote.lock.takeover')
            try:
                takeover = guard.open('x')
            except FileExistsError:
                raise ValueError('Tomada do lock de promoção em andamento')
            try:
                takeover.close()
                try:
                    age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age < 600:
                    raise ValueError('Promoção/rollback em andamento: state/promote.lock')
                logging.getLogger(__name__).warning('Tomando lock de promoção obsoleto: %s (%.0f s)', lock, age)
                lock.unlink(missing_ok=True)
                try:
                    stream = lock.open('x', encoding='utf-8')
                except FileExistsError:
                    raise ValueError('Promoção/rollback em andamento: state/promote.lock')
                break
            finally:
                guard.unlink(missing_ok=True)
    try:
        with stream:
            stream.write(str(os.getpid()) + '\n')
        yield
    finally:
        lock.unlink(missing_ok=True)


def promote(root, now=None):
    root = Path(root)
    with _promotion_lock(root):
        return _promote(root, now)


def _promote(root, now=None):
    root = Path(root)
    data = _read(root / 'candidate.json')
    gates = data.get('gates', {})
    if (not isinstance(gates, dict) or gates.get('doctor') is not True or gates.get('fixture') is not True
            or gates.get('revocation', 'ok') not in ('ok', 'offline')):
        raise ValueError('Gates de promoção não satisfeitos')
    if not _release(root, data.get('release_id')).is_dir():
        raise ValueError('Release ausente')
    try:
        previous = _read(root / 'current.json')
        _release(root, previous.get('release_id'))
    except FileNotFoundError:
        if (root / 'current.json').is_symlink():
            raise ValueError('current.json existente e ilegível; promoção recusada')
        previous = None
    except (OSError, ValueError) as exc:
        raise ValueError('current.json existente e ilegível; promoção recusada: ' + str(exc)) from exc
    if previous:
        _atomic(root / 'state' / 'history' / (_release(root, previous.get('release_id')).name + '.json'), previous)
    data['previous_release_id'] = (previous.get('previous_release_id') if previous.get('release_id') == data['release_id']
                                   else previous.get('release_id')) if previous else None
    instant = now or datetime.now(timezone.utc)
    data['promoted_at'] = instant.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if isinstance(instant, datetime) else str(instant)
    _atomic(root / 'current.json', data)
    (root / 'candidate.json').unlink(missing_ok=True)
    return data


def rollback(root):
    root = Path(root)
    with _promotion_lock(root):
        return _rollback(root)


def _rollback(root):
    root = Path(root)
    data = _read(root / 'current.json')
    previous = data.get('previous_release_id')
    if not _release(root, previous).is_dir():
        raise ValueError('Release anterior ausente')
    # Keep the previous manifest: profiles, OCR and explicit python may differ.
    saved = _optional(root / 'state' / 'history' / (previous + '.json'))
    if saved is None:
        saved = dict(data, release_id=previous, python=str(release_python(root, previous)))
    _atomic(root / 'state' / 'history' / (_release(root, data.get('release_id')).name + '.json'), data)
    saved['previous_release_id'] = data['release_id']
    saved['promoted_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    _atomic(root / 'current.json', saved)
    return saved


def where(root=None, *, platform=None, env=None) -> dict:
    from . import __version__
    platform = platform or sys.platform
    root = Path(root) if root is not None else managed_root(platform=platform, env=env)
    errors = []
    def read(path):
        try:
            data = _read(path)
            _release(root, data.get('release_id'))
            return data
        except FileNotFoundError as exc:
            if path.is_symlink():
                errors.append(f'{path}: {exc}')
            return None
        except (OSError, ValueError) as exc:
            errors.append(f'{path}: {exc}')
            return None
    current, candidate = read(root / 'current.json'), read(root / 'candidate.json')
    python = None
    if current:
        try:
            python = current.get('python') or str(release_python(root, current.get('release_id'), platform=platform))
        except ValueError as exc:
            errors.append(str(exc))
    legacy = [{'root': str(path), 'current': read(path / 'current.json')}
              for path in legacy_roots(platform=platform, env=env) if path.is_dir()]
    return dict(schema='traslado-where-1', version=__version__, root=str(root), current=current,
                candidate=candidate, python=python, module='traslado', legacy_roots=legacy,
                platform=platform, arch=host.machine(), errors=errors)


def _desktop(home, platform=None, injected=False):
    if (platform or sys.platform) == 'win32' and not injected:
        import ctypes
        import uuid
        folder = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID('B4BFCC3A-DB2C-424C-B029-7FE99A87C641').bytes_le)
        value = ctypes.c_wchar_p()
        try:
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(value)) == 0:
                try:
                    return Path(value.value)
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(value)
        except (OSError, AttributeError):
            pass
    return home / 'Desktop'


def _locations(platform, home, desktop_dir, start_menu_dir, applications_dir, icons_dir, injected=False):
    return (Path(desktop_dir) if desktop_dir else _desktop(home, platform, injected),
            Path(start_menu_dir) if start_menu_dir else Path(os.environ.get('APPDATA', home / 'AppData/Roaming') if platform == 'win32' and not injected else home / 'AppData/Roaming') / 'Microsoft/Windows/Start Menu/Programs',
            Path(applications_dir) if applications_dir else home / ('Applications' if platform == 'darwin' else '.local/share/applications'),
            Path(icons_dir) if icons_dir else home / '.local/share/icons/hicolor/256x256/apps')


def _shortcut_stem(name):
    normalized = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    stem = re.sub(r'[^a-z0-9_-]+', '-', normalized).strip('-')
    if not stem:
        raise ValueError('Nome de atalho inválido')
    return stem


def _desktop_exec(value):
    # Exec argument quoting, followed by the general Desktop Entry string escape.
    value = str(value).replace('%', '%%')
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return value.replace('\\', '\\\\')


def create_shortcut(target_python, name='Traslado', desktop=True, start_menu=True, *,
                    platform=None, home=None, desktop_dir=None, start_menu_dir=None,
                    applications_dir=None, icons_dir=None) -> list[Path]:
    injected = home is not None
    platform, home = platform or sys.platform, Path(home) if injected else Path.home()
    _release('.', name)  # disallow path components in shortcut names
    desk, menu, apps, icons = _locations(platform, home, desktop_dir, start_menu_dir, applications_dir, icons_dir, injected)
    target = Path(target_python).absolute()
    icon = Path(__file__).parent / 'assets/traslado.png'
    result = []
    if platform == 'win32':
        target = target.with_name('pythonw.exe')
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        for folder, enabled in ((desk, desktop), (menu, start_menu)):
            if enabled:
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (name + '.lnk')
                script = ("$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; "
                          f'$l=$s.CreateShortcut({quote(path)}); $l.TargetPath={quote(target)}; '
                          f"$l.Arguments='-I -m traslado.gui'; $l.WorkingDirectory={quote(target.parent)}; $l.Save()")
                subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], check=True,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                result.append(path)
    elif platform == 'darwin':
        app = apps / (name + '.app')
        executable = app / 'Contents/MacOS/Traslado'
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text('#!/bin/sh\nexec ' + shlex.quote(str(target)) + ' -I -m traslado.gui "$@"\n', encoding='utf-8')
        executable.chmod(0o755)
        with (app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump(dict(CFBundleName=name, CFBundleExecutable='Traslado', CFBundleIdentifier='org.traslado.desktop', CFBundlePackageType='APPL'), stream)
        result.append(app)
    else:
        apps.mkdir(parents=True, exist_ok=True)
        icons.mkdir(parents=True, exist_ok=True)
        stem = _shortcut_stem(name)
        installed_icon = icons / (stem + '.png')
        shutil.copyfile(icon, installed_icon)
        escaped = _desktop_exec(target)
        path = apps / (stem + '.desktop')
        path.write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec="{escaped}" -I -m traslado.gui\nIcon={installed_icon}\nTerminal=false\nCategories=Office;\n', encoding='utf-8')
        result.extend([path, installed_icon])
        if platform == sys.platform and shutil.which('update-desktop-database'):
            subprocess.run(['update-desktop-database', str(apps)], check=False)
    return result


def remove_shortcuts(names, *, platform=None, home=None, desktop_dir=None, start_menu_dir=None,
                     applications_dir=None, icons_dir=None) -> list[Path]:
    injected = home is not None
    platform, home = platform or sys.platform, Path(home) if injected else Path.home()
    desk, menu, apps, icons = _locations(platform, home, desktop_dir, start_menu_dir, applications_dir, icons_dir, injected)
    paths = []
    for name in names:
        _release('.', name)
        paths.extend([desk / (name + '.lnk'), menu / (name + '.lnk')] if platform == 'win32' else
                     [apps / (name + '.app')] if platform == 'darwin' else [apps / (_shortcut_stem(name) + '.desktop'), icons / (_shortcut_stem(name) + '.png')])
    removed = []
    for path in dict.fromkeys(paths):
        if path.exists():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path)
    return removed


def open_folder(path):
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])


def enable_hidpi():
    if sys.platform == 'win32':
        import ctypes
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (OSError, AttributeError):
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass
