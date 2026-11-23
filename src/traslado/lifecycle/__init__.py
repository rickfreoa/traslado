# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Ciclo de vida da instalação gerenciada: setup, where, update, uninstall, rollback e migração.

Fachada estável (E1 da 3.0.0). A implementação foi decomposta, sem mudança de comportamento, em:
`_common` (constantes e normalização), `releases` (uso, remoção, poda, integridade), `ocr_setup` (OCR),
`legacy_windows` (instalação legada), `gates` (doctor, fixture, digests), `install` (setup_release),
`uninstall` e `update`. Quem substitui uma função em teste deve fazê-lo no módulo que a define.
"""
from __future__ import annotations

# Nomes que o módulo único expunha antes da decomposição: continuam acessíveis por compatibilidade.
from datetime import datetime, timezone
import base64
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import platform as host
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Callable

from .. import __version__
from .. import doctor
from .. import ocr
from .. import platform as plat
from .. import revocation
from .. import runner
from ..model import Options
from ..pathguard import unsafe_link

from ._common import (DEFAULT_LANGUAGES, POWERSHELL_WIN, REPOSITORY_URL, ShortcutError, _shortcut_dirs_from_env,
                      _utc_now, compute_release_id, normalize_extras, normalize_languages, validate_version)
from .releases import (_managed_python_exe, _remove_unreferenced_release_locked, active_release_matches,
                       prune_releases, release_in_use, release_integrity, remove_unreferenced_release)
from .ocr_setup import (_TESSERACT_LINUX, _assert_pe_architecture, _assert_platform_architecture, _find_bootstrap_ps1,
                        _find_ocr_lock, _get_windows_native_machine, _install_tessdata_from_lock,
                        _parse_tesseract_version, _purge_seven_zip, _read_pe_machine, linux_tesseract_hint, setup_ocr)
from .legacy_windows import handle_legacy_windows
from .gates import evaluate_doctor_gate, run_fixture_gate, validate_bootstrap_digests
from .install import setup_release
from .uninstall import _defer_self_removal, discard_pending_uninstall, uninstall_managed
from .update import update_release
