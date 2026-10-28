#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Executa o E2E dentro de contêiner Linux limpo (Debian bookworm-slim ou Fedora minimal 42).
set -eu

DISTRO_LABEL="${1:-linux}"
DIST_DIR="${2:-/dist}"
OUT_DIR="${3:-/out}"
WORKSPACE_DIR="${4:-/workspace}"

check_absent() {
    cmd_name="$1"
    if command -v "$cmd_name" >/dev/null 2>&1; then
        echo "ERRO: '$cmd_name' já estava presente na imagem limpa ($(command -v "$cmd_name"))" >&2
        exit 1
    fi
}

echo "=== 1. Conferindo ausência inicial de python3, python, wish (tk), uv e tesseract ==="
for c in python3 python wish wish8.6 tclsh tclsh8.6 uv tesseract; do
    check_absent "$c"
done

OS_PRETTY="unknown"
if [ -f /etc/os-release ]; then
    OS_PRETTY="$(grep '^PRETTY_NAME=' /etc/os-release | cut -d= -f2- | tr -d '"' || echo unknown)"
fi

INSTALLED_PREREQS=""
NEED_TOOLS=""
mkdir -p "$OUT_DIR"
{ cat /etc/os-release; if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W; else rpm -qa; fi; } > "$OUT_DIR/inventario-inicial-$DISTRO_LABEL.txt"

for t in curl; do
    if ! command -v "$t" >/dev/null 2>&1; then
        NEED_TOOLS="$NEED_TOOLS $t"
    fi
done
if [ ! -d /etc/ssl/certs ] && [ ! -f /etc/pki/tls/certs/ca-bundle.crt ]; then
    NEED_TOOLS="$NEED_TOOLS ca-certificates"
fi

if [ -n "$NEED_TOOLS" ]; then
    echo "=== 2. Instalando pré-requisitos mínimos de rede/arquivo:$NEED_TOOLS ==="
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -y >/dev/null
        apt-get install -y --no-install-recommends curl ca-certificates >/dev/null
        INSTALLED_PREREQS="apt-get: curl ca-certificates"
    elif command -v microdnf >/dev/null 2>&1; then
        microdnf install -y curl-minimal ca-certificates >/dev/null
        INSTALLED_PREREQS="microdnf: curl-minimal ca-certificates"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y curl-minimal ca-certificates >/dev/null
        INSTALLED_PREREQS="dnf: curl-minimal ca-certificates"
    fi
else
    INSTALLED_PREREQS="none (curl, ca-certificates, tar, gzip já presentes na imagem mínima)"
fi

# tar e gzip são pré-requisitos documentados do install.sh (descompactam uv e o Python gerenciado);
# imagens mínimas como fedora-minimal não os trazem, e o E2E os instala, registrando no inventário.
NEED_ARCHIVE=""
for tool in tar gzip; do
    command -v "$tool" >/dev/null 2>&1 || NEED_ARCHIVE="$NEED_ARCHIVE $tool"
done
if [ -n "$NEED_ARCHIVE" ]; then
    echo "=== 2b. Instalando pré-requisitos de arquivo:$NEED_ARCHIVE ==="
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -y >/dev/null; apt-get install -y --no-install-recommends $NEED_ARCHIVE >/dev/null
        INSTALLED_PREREQS="$INSTALLED_PREREQS; apt-get:$NEED_ARCHIVE"
    elif command -v microdnf >/dev/null 2>&1; then
        microdnf install -y $NEED_ARCHIVE >/dev/null
        INSTALLED_PREREQS="$INSTALLED_PREREQS; microdnf:$NEED_ARCHIVE"
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y $NEED_ARCHIVE >/dev/null
        INSTALLED_PREREQS="$INSTALLED_PREREQS; dnf:$NEED_ARCHIVE"
    fi
    for tool in tar gzip; do
        command -v "$tool" >/dev/null 2>&1 || { echo "ERRO: $tool ausente após instalação" >&2; exit 1; }
    done
fi

echo "=== 3. Reconfirmando ausência de python3, python, wish (tk), uv e tesseract após pré-requisitos ==="
for c in python3 python wish wish8.6 tclsh tclsh8.6 uv tesseract; do
    check_absent "$c"
done

mkdir -p /tmp/e2e-work "$OUT_DIR"
cat > "$OUT_DIR/pre-inventory-$DISTRO_LABEL.json" <<EOF
{
  "distro_label": "$DISTRO_LABEL",
  "os_pretty_name": "$OS_PRETTY",
  "initial_absent_verified": ["python3", "python", "wish", "uv", "tesseract"],
  "installed_prerequisites": "$INSTALLED_PREREQS"
}
EOF

HOME="/tmp/e2e-work/home"
export HOME
export XDG_DATA_HOME="$HOME/.local/share"
export TRASLADO_PROMOCAO_OFFLINE="teste"
export TMPDIR="/tmp/e2e-work"
mkdir -p "$HOME" "$XDG_DATA_HOME"

MANAGED_ROOT="$XDG_DATA_HOME/traslado"

echo "=== 4. Testando install.sh --from-dir com --no-shortcut ==="
sh "$DIST_DIR/install.sh" --from-dir "$DIST_DIR" --root "$MANAGED_ROOT" --yes --no-shortcut --allow-offline-promotion
if [ -f "$XDG_DATA_HOME/applications/traslado.desktop" ]; then
    echo "ERRO: atalho .desktop foi criado mesmo com --no-shortcut" >&2
    exit 1
fi

echo "=== 5. Executando install.sh --from-dir padrão (com atalhos .desktop e .png) ==="
VENV_PY="$(echo "$MANAGED_ROOT"/releases/*/.venv/bin/python)"
"$VENV_PY" -I -m traslado uninstall --yes --root "$MANAGED_ROOT"
test ! -e "$MANAGED_ROOT"
sh "$DIST_DIR/install.sh" --from-dir "$DIST_DIR" --root "$MANAGED_ROOT" --yes --allow-offline-promotion

VENV_PY="$(echo "$MANAGED_ROOT"/releases/*/.venv/bin/python)"
if [ -z "$VENV_PY" ] || [ ! -x "$VENV_PY" ]; then
    echo "ERRO: Python gerenciado não encontrado em $MANAGED_ROOT/releases" >&2
    exit 1
fi

echo "=== 6. Executando verificações E2E, auditoria de bibliotecas, update, rollback e uninstall ==="
"$VENV_PY" -I -X utf8 "$WORKSPACE_DIR/scripts/e2e_runner.py" \
    --mode linux-container \
    --root "$MANAGED_ROOT" \
    --dist "$DIST_DIR" \
    --pre-inventory "$OUT_DIR/pre-inventory-$DISTRO_LABEL.json" \
    --saida "$OUT_DIR/e2e-$DISTRO_LABEL.json" \
    --audit-saida "$OUT_DIR/auditoria-libs-$DISTRO_LABEL.json"
