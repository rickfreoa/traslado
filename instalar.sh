#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Fonte correspondente: https://github.com/rickfreoa/traslado/tree/v2.0.0
set -eu
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec sh "$DIR/install.sh" --from-dir "$DIR" "$@"
