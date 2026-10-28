# SPDX-License-Identifier: AGPL-3.0-or-later
"""Consolida medições E2 por SO; ausência de prova nunca vira zero."""
import argparse
import json
from pathlib import Path


def consolidate(folder):
    reports = {}
    for path in folder.rglob('contencao-*.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        key = data.get('platform')
        if key in reports:
            raise ValueError(f'Medição duplicada para {key}: {path}')
        reports[key] = data
    rows = ['| SO/caso | Imposto / monitorado | Pico MiB | Teto MiB | Encerramento s | Remanescentes processos / temporários |',
            '|---|---|---:|---:|---:|---|']
    complete = True
    for platform, mechanism in [('win32','Job Object / prazo'), ('linux','RLIMIT_AS / RSS e prazo'), ('darwin','Não / RSS e prazo')]:
        data = reports.get(platform, {})
        if not data.get('success'):
            complete = False
        for case in ('child', 'grandchild', 'timeout'):
            item = data.get(case)
            if not item:
                rows.append(f'| {platform}/{case} | {mechanism} | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |')
                complete = False
                continue
            rows.append(f"| {platform}/{case} | {mechanism} | {item['peak_bytes']/1048576:.3f} | "
                        f"{item['limit_bytes']/1048576:.0f} | {item['elapsed_s']:.3f} | "
                        f"{len(item['remaining_processes'])} / {item['remaining_temporaries']} |")
    return '\n'.join(rows)+'\n', complete


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('folder', type=Path)
    ap.add_argument('--saida', type=Path, required=True)
    args = ap.parse_args()
    table, complete = consolidate(args.folder)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(table, encoding='utf-8')
    print(table)
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
