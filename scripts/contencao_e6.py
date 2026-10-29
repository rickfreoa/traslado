# SPDX-License-Identifier: AGPL-3.0-or-later
"""Executa as provas E2 existentes e preserva medições mesmo se uma prova falhar."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--saida', type=Path, required=True)
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_containment_e2 import ContainmentE2
    source = ROOT / 'tests/_medicoes' / ('contencao-' + sys.platform + '.json')
    source.unlink(missing_ok=True)
    # Classe inteira: as provas de estouro e também os parsers de RSS e a contagem de rasters do OCR.
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ContainmentE2)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    report = json.loads(source.read_text(encoding='utf-8')) if source.exists() else {}
    report['success'] = result.wasSuccessful()
    report['platform'] = sys.platform
    report['memory_enforced_by_os'] = sys.platform in ('win32', 'linux')
    report['memory_mechanism'] = {'win32': 'Job Object: committed job memory',
                                  'linux': 'RLIMIT_AS per process and sampled process-group RSS',
                                  'darwin': 'Sampled process-group RSS; no kernel memory ceiling'}.get(sys.platform, 'unsupported')
    report['peak_measurement'] = 'PeakJobMemoryUsed (committed bytes)' if sys.platform == 'win32' else 'sampled aggregate RSS'
    report['supervisor_monitors'] = ['deadline', 'disk', 'log'] + ([] if sys.platform == 'win32' else ['process-group RSS'])
    args.saida.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
