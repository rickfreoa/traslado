# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Compatibilidade de módulos com Traslado durante a série 2.x."""
import importlib
import importlib.abc
import importlib.util
import sys


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target):
        self.target = target

    def create_module(self, spec):
        module = importlib.import_module(self.target)
        self.spec = module.__spec__
        return module

    def exec_module(self, module):
        module.__spec__ = self.spec

    def get_filename(self, fullname):
        # runpy sets argv[0] from spec.origin; argparse on Python 3.11-3.13
        # requires a filename there, even when the code comes from an alias.
        return importlib.util.find_spec(self.target).origin

    def get_code(self, fullname):
        # runpy requests code without create_module/exec_module. Execute the
        # canonical module as __main__, keeping its relative imports canonical.
        return compile(
            "import sys, runpy\n"
            "print('Depreciação: use python -m traslado em vez de freoa_v6.', file=sys.stderr)\n"
            f"runpy.run_module({self.target!r}, run_name='__main__')\n",
            f"<alias {fullname}>", "exec")


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('freoa_v6.'):
            return None
        canonical = 'traslado' + fullname[len('freoa_v6'):]
        spec = importlib.util.find_spec(canonical)
        if spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(canonical),
            is_package=spec.submodule_search_locations is not None)


sys.meta_path.insert(0, _AliasFinder())
sys.modules[__name__] = importlib.import_module('traslado')
