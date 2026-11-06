# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from traslado import platform
try:
    from tests.profile_safety import guard_shortcut_locations
except ImportError:
    from profile_safety import guard_shortcut_locations


class ProfileSafetyTests(unittest.TestCase):
    def test_guard_rejects_every_external_shortcut_directory_before_io(self):
        with tempfile.TemporaryDirectory() as raw:
            outer = Path(raw)
            home = outer / "profile"
            home.mkdir()
            outside = outer / "outside"
            outside.mkdir()
            sentinel = outside / "Traslado.lnk"
            sentinel.write_bytes(b"preservar")
            for system in ("win32", "linux", "darwin"):
                for key in ("desktop_dir", "start_menu_dir", "applications_dir", "icons_dir"):
                    with self.subTest(system=system, key=key), guard_shortcut_locations(home), \
                         patch.object(platform.subprocess, "run") as run:
                        args = dict(platform=system, home=home, **{key: outside})
                        with self.assertRaisesRegex(AssertionError, "fora do temporario"):
                            platform.create_shortcut("python", **args)
                        with self.assertRaisesRegex(AssertionError, "fora do temporario"):
                            platform.remove_shortcuts(["Traslado"], **args)
                        run.assert_not_called()
                        self.assertEqual(sentinel.read_bytes(), b"preservar")
                        self.assertEqual(list(outside.iterdir()), [sentinel])

    def test_guard_rejects_home_fallback_outside_test_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            with guard_shortcut_locations(base / "profile"), \
                 patch.object(Path, "home", return_value=base / "outside"), \
                 patch.object(platform.subprocess, "run") as run:
                for action, arg in ((platform.create_shortcut, "python"),
                                    (platform.remove_shortcuts, ["Traslado"])):
                    with self.assertRaisesRegex(AssertionError, "fora do temporario"):
                        action(arg, platform="linux")
                run.assert_not_called()
                self.assertFalse((base / "outside").exists())
