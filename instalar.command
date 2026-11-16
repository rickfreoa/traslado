#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Fonte correspondente da compilação local: traslado-2.0.2.tar.gz
# Referência da árvore em caso de publicação: https://github.com/rickfreoa/traslado/tree/v2.0.2
set -eu
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec sh "$DIR/install.sh" --from-dir "$DIR" "$@"
