#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Fonte correspondente: https://github.com/rickfreoa/traslado/tree/v2.0.0
# Códigos de saída: 0 = instalado/ativo; 1 = erro de download, hash ou attestation; 2 = falha nos gates ou parâmetros.
main() {
set -eu

VERSION="2.0.0"
PROFILE="Full"
EXTRAS=""
LANGUAGES="por,eng,spa,ita,fra,deu"
NO_SHORTCUT=0
ROOT=""
FROM_DIR=""
YES=0
ALLOW_OFFLINE_PROMOTION=0
SKIP_OCR=0
OCR_CACHE=""

UV_VERSION="0.13.6"
UV_HASH_LINUX_X64="f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645"
UV_HASH_LINUX_ARM64="229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d"
UV_HASH_DARWIN_ARM64="60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472"
UV_HASH_DARWIN_X64="63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02"
UV_HASH_WIN_X64="a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f"

while [ $# -gt 0 ]; do
    case "$1" in
        --version|-Version)
            VERSION="$2"; shift 2 ;;
        --profile|-Profile)
            PROFILE="$2"; shift 2 ;;
        --extras|-Extras)
            EXTRAS="$2"; shift 2 ;;
        --languages|-Languages)
            LANGUAGES="$2"; shift 2 ;;
        --no-shortcut|-NoShortcut)
            NO_SHORTCUT=1; shift 1 ;;
        --root|-Root)
            ROOT="$2"; shift 2 ;;
        --from-dir|-FromDir)
            FROM_DIR="$2"; shift 2 ;;
        --yes|-Yes|-y)
            YES=1; shift 1 ;;
        --allow-offline-promotion|-AllowOfflinePromotion)
            ALLOW_OFFLINE_PROMOTION=1; shift 1 ;;
        --skip-ocr|-SkipOCR)
            SKIP_OCR=1; shift 1 ;;
        --ocr-cache|-OCRCache)
            OCR_CACHE="$2"; shift 2 ;;
        *)
            echo "Parâmetro desconhecido: $1" >&2
            exit 2 ;;
    esac
done

CLEAN_VERSION="${VERSION#v}"
case "$PROFILE" in
    Core|core) NORM_PROFILE="Core"; PROFILE_LOWER="core" ;;
    Full|full) NORM_PROFILE="Full"; PROFILE_LOWER="full" ;;
    *) echo "Perfil inválido: $PROFILE (use Full ou Core)" >&2; exit 2 ;;
esac

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"

if [ -z "$ROOT" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        ROOT="$HOME/Library/Application Support/Traslado"
    else
        XDG="${XDG_DATA_HOME:-$HOME/.local/share}"
        ROOT="$XDG/traslado"
    fi
fi
mkdir -p "$ROOT"
ROOT="$(CDPATH= cd -- "$ROOT" && pwd)"

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | { read -r h _; printf "%s\n" "$h" | tr '[:upper:]' '[:lower:]'; }
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | { read -r h _; printf "%s\n" "$h" | tr '[:upper:]' '[:lower:]'; }
    else
        openssl dgst -sha256 "$1" | sed 's/^.*= *//' | tr '[:upper:]' '[:lower:]'
    fi
}

sha256_string() {
    if command -v sha256sum >/dev/null 2>&1; then
        printf "%s" "$1" | sha256sum | { read -r h _; printf "%s\n" "$h" | tr '[:upper:]' '[:lower:]'; }
    elif command -v shasum >/dev/null 2>&1; then
        printf "%s" "$1" | shasum -a 256 | { read -r h _; printf "%s\n" "$h" | tr '[:upper:]' '[:lower:]'; }
    else
        printf "%s" "$1" | openssl dgst -sha256 | sed 's/^.*= *//' | tr '[:upper:]' '[:lower:]'
    fi
}

download_https() {
    url="$1"
    dest="$2"
    case "$url" in
        https://*) ;;
        *) echo "URL não HTTPS recusada: $url" >&2; exit 1 ;;
    esac
    mkdir -p "$(dirname "$dest")"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest" || exit 1
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$dest" "$url" || exit 1
    else
        echo "Nem curl nem wget disponíveis para download." >&2
        exit 1
    fi
}

find_local() {
    base="$1"
    shift
    for rel in "$@"; do
        if [ -f "$base/$rel" ]; then
            echo "$base/$rel"
            return 0
        fi
    done
    return 1
}

# Read only the active interpreter path; the installed CLI validates profile and integrity.
if [ -f "$ROOT/current.json" ]; then
    ACTIVE_ID="$(sed -n 's/.*"release_id": "\([^"]*\)".*/\1/p' "$ROOT/current.json")"
    case "$ACTIVE_ID" in
        *[!a-zA-Z0-9_.-]*|'') ;;
        *) ACTIVE_PY="$ROOT/releases/$ACTIVE_ID/.venv/bin/python"
           if [ -x "$ACTIVE_PY" ] && "$ACTIVE_PY" -I -m traslado setup --check-active --expected-version "$CLEAN_VERSION" --root "$ROOT" --profile "$NORM_PROFILE" --extras "$EXTRAS" --languages "$LANGUAGES"; then
               echo "Traslado $CLEAN_VERSION ($NORM_PROFILE) ja esta ativo e integro."
               return 0
           fi ;;
    esac
fi
# Fresh candidate every time; mkdir below refuses collisions.
RELEASE_ID="${CLEAN_VERSION}_$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"

# 1. Obter uv fixado por versão e SHA-256
if [ -n "${TRASLADO_TEST_UV:-}" ] && [ -x "${TRASLADO_TEST_UV}" ]; then
    UV_EXE="$TRASLADO_TEST_UV"
else
    UV_DIR="$ROOT/tools/uv-$UV_VERSION"
    if [ -x "$UV_DIR/uv" ]; then
        UV_EXE="$UV_DIR/uv"
    else
        case "$OS_NAME:$ARCH_NAME" in
            Linux:x86_64|Linux:amd64)
                UV_PLATFORM="x86_64-unknown-linux-gnu"
                EXPECTED_UV_HASH="$UV_HASH_LINUX_X64" ;;
            Linux:aarch64|Linux:arm64)
                UV_PLATFORM="aarch64-unknown-linux-gnu"
                EXPECTED_UV_HASH="$UV_HASH_LINUX_ARM64" ;;
            Darwin:arm64|Darwin:aarch64)
                UV_PLATFORM="aarch64-apple-darwin"
                EXPECTED_UV_HASH="$UV_HASH_DARWIN_ARM64" ;;
            Darwin:x86_64)
                UV_PLATFORM="x86_64-apple-darwin"
                EXPECTED_UV_HASH="$UV_HASH_DARWIN_X64" ;;
            *)
                echo "Plataforma não suportada pelo instalador: $OS_NAME / $ARCH_NAME" >&2
                exit 2 ;;
        esac
        UV_ARCHIVE="uv-$UV_PLATFORM.tar.gz"
        UV_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ARCHIVE"
        mkdir -p "$UV_DIR"
        echo "Baixando uv $UV_VERSION ($UV_PLATFORM)..."
        download_https "$UV_URL" "$UV_DIR/$UV_ARCHIVE"
        ACTUAL_UV_HASH="$(sha256_file "$UV_DIR/$UV_ARCHIVE")"
        if [ "$ACTUAL_UV_HASH" != "$EXPECTED_UV_HASH" ]; then
            rm -f "$UV_DIR/$UV_ARCHIVE"
            echo "SHA-256 do uv divergente: esperado $EXPECTED_UV_HASH, obtido $ACTUAL_UV_HASH" >&2
            exit 1
        fi
        tar -xzf "$UV_DIR/$UV_ARCHIVE" -C "$UV_DIR"
        rm -f "$UV_DIR/$UV_ARCHIVE"
        if [ -x "$UV_DIR/uv" ]; then
            UV_EXE="$UV_DIR/uv"
        elif [ -x "$UV_DIR/uv-$UV_PLATFORM/uv" ]; then
            mv "$UV_DIR/uv-$UV_PLATFORM/uv" "$UV_DIR/uv"
            UV_EXE="$UV_DIR/uv"
        else
            echo "Binário uv não encontrado após extração." >&2
            exit 1
        fi
    fi
fi

# 2. Obter e conferir artefatos (SHA256SUMS, wheel e locks)
WHEEL_NAME="traslado-$CLEAN_VERSION-py3-none-any.whl"
PROFILE_LOCK_NAME="requirements-$PROFILE_LOWER.hashes.lock"
LEGACY_LOCK_NAME="requirements-legacy.hashes.lock"
RELEASE_BASE_URL="https://github.com/rickfreoa/traslado/releases/download/v$CLEAN_VERSION"
STAGE_DOWNLOADS="$ROOT/state/downloads/v$CLEAN_VERSION"
mkdir -p "$STAGE_DOWNLOADS"

check_sum_entry() {
    sums_file="$1"
    entry_name="$2"
    file_path="$3"
    expected=""
    while read -r sum name _; do
        clean_name="${name#\*}"
        if [ "$clean_name" = "$entry_name" ]; then
            expected="$(printf "%s" "$sum" | tr '[:upper:]' '[:lower:]')"
            break
        fi
    done < "$sums_file"
    if [ -z "$expected" ]; then
        echo "Entrada $entry_name ausente em SHA256SUMS" >&2
        exit 1
    fi
    actual="$(sha256_file "$file_path")"
    if [ "$actual" != "$expected" ]; then
        echo "SHA-256 divergente para $entry_name: esperado $expected, obtido $actual" >&2
        exit 1
    fi
}

LEGACY_LOCK_PATH=""
if [ -n "$FROM_DIR" ]; then
    FROM_DIR="$(CDPATH= cd -- "$FROM_DIR" && pwd)"
    SUMS_PATH="$(find_local "$FROM_DIR" "SHA256SUMS" "../SHA256SUMS" "PAYLOAD-SHA256SUMS" || true)"
    WHEEL_PATH="$(find_local "$FROM_DIR" "$WHEEL_NAME" "dist/$WHEEL_NAME" || true)"
    PROFILE_LOCK_PATH="$(find_local "$FROM_DIR" "$PROFILE_LOCK_NAME" "locks/$PROFILE_LOCK_NAME" || true)"
    if [ -z "$WHEEL_PATH" ] || [ -z "$PROFILE_LOCK_PATH" ] || [ -z "$SUMS_PATH" ]; then
        echo "Artefatos obrigatórios (SHA256SUMS, $WHEEL_NAME, $PROFILE_LOCK_NAME) ausentes em $FROM_DIR" >&2
        exit 1
    fi
    check_sum_entry "$SUMS_PATH" "$WHEEL_NAME" "$WHEEL_PATH"
    check_sum_entry "$SUMS_PATH" "$PROFILE_LOCK_NAME" "$PROFILE_LOCK_PATH"
    case ",$EXTRAS," in
        *,legacy,*)
            LEGACY_LOCK_PATH="$(find_local "$FROM_DIR" "$LEGACY_LOCK_NAME" "locks/$LEGACY_LOCK_NAME" || true)"
            if [ -z "$LEGACY_LOCK_PATH" ]; then
                echo "Lock $LEGACY_LOCK_NAME ausente em $FROM_DIR" >&2
                exit 1
            fi
            check_sum_entry "$SUMS_PATH" "$LEGACY_LOCK_NAME" "$LEGACY_LOCK_PATH" ;;
    esac
else
    SUMS_PATH="$STAGE_DOWNLOADS/SHA256SUMS"
    WHEEL_PATH="$STAGE_DOWNLOADS/$WHEEL_NAME"
    PROFILE_LOCK_PATH="$STAGE_DOWNLOADS/$PROFILE_LOCK_NAME"
    echo "Baixando SHA256SUMS, $WHEEL_NAME e $PROFILE_LOCK_NAME de $RELEASE_BASE_URL..."
    download_https "$RELEASE_BASE_URL/SHA256SUMS" "$SUMS_PATH"
    download_https "$RELEASE_BASE_URL/$WHEEL_NAME" "$WHEEL_PATH"
    download_https "$RELEASE_BASE_URL/$PROFILE_LOCK_NAME" "$PROFILE_LOCK_PATH"
    check_sum_entry "$SUMS_PATH" "$WHEEL_NAME" "$WHEEL_PATH"
    check_sum_entry "$SUMS_PATH" "$PROFILE_LOCK_NAME" "$PROFILE_LOCK_PATH"
    case ",$EXTRAS," in
        *,legacy,*)
            LEGACY_LOCK_PATH="$STAGE_DOWNLOADS/$LEGACY_LOCK_NAME"
            download_https "$RELEASE_BASE_URL/$LEGACY_LOCK_NAME" "$LEGACY_LOCK_PATH"
            check_sum_entry "$SUMS_PATH" "$LEGACY_LOCK_NAME" "$LEGACY_LOCK_PATH" ;;
    esac
fi

DIGESTS_PATH="$STAGE_DOWNLOADS/verified-$RELEASE_ID.json"
{
    printf '{"%s":"%s","%s":"%s"' "$WHEEL_NAME" "$(sha256_file "$WHEEL_PATH")" "$PROFILE_LOCK_NAME" "$(sha256_file "$PROFILE_LOCK_PATH")"
    if [ -n "$LEGACY_LOCK_PATH" ]; then
        printf ',"%s":"%s"' "$LEGACY_LOCK_NAME" "$(sha256_file "$LEGACY_LOCK_PATH")"
    fi
    if [ "$(basename "$SUMS_PATH")" = "SHA256SUMS" ]; then
        printf ',"SHA256SUMS":"%s"' "$(sha256_file "$SUMS_PATH")"
    fi
    printf '}\n'
} > "$DIGESTS_PATH"

# 3. Verificação de attestation (gh)
if command -v gh >/dev/null 2>&1; then
    echo "Verificando attestation criptográfica com gh..."
    for artifact in "$WHEEL_PATH" "$PROFILE_LOCK_PATH" "$LEGACY_LOCK_PATH" "$SUMS_PATH"; do
        [ -n "$artifact" ] || continue
        gh attestation verify "$artifact" --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml || exit 1
    done
else
    echo "Aviso: verificação automática via GitHub CLI (gh) não executada neste modo."
    echo "O controle principal de autenticidade é manual:"
    echo "  gh attestation verify \"$WHEEL_PATH\" --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml"
fi

# 4. Criar .venv com Python 3.13 gerenciado
RELEASE_DIR="$ROOT/releases/$RELEASE_ID"
mkdir -p "$ROOT/releases"
mkdir "$RELEASE_DIR" || exit 2

if [ -n "$FROM_DIR" ]; then
    LOCAL_OCR_LOCK="$(find_local "$FROM_DIR" "ocr-assets.lock.json" "../ocr-assets.lock.json" || true)"
    if [ -n "$LOCAL_OCR_LOCK" ]; then
        cp "$LOCAL_OCR_LOCK" "$RELEASE_DIR/ocr-assets.lock.json"
    fi
fi

export UV_PYTHON_INSTALL_DIR="$ROOT/python"
export UV_CACHE_DIR="$ROOT/state/uv-cache"
mkdir -p "$UV_PYTHON_INSTALL_DIR"

VENV_DIR="$RELEASE_DIR/.venv"
echo "Criando ambiente Python 3.13 gerenciado em $VENV_DIR..."
"$UV_EXE" venv "$VENV_DIR" --python 3.13 --python-preference only-managed || exit 2
VENV_PYTHON="$VENV_DIR/bin/python"

if [ "$NORM_PROFILE" = "Full" ]; then
    echo "Instalando dependências fixadas ($PROFILE_LOCK_NAME)..."
    "$UV_EXE" pip install --python "$VENV_PYTHON" --require-hashes --only-binary :all: -r "$PROFILE_LOCK_PATH" || exit 2
fi
if [ -n "$LEGACY_LOCK_PATH" ]; then
    echo "Instalando extra experimental legacy ($LEGACY_LOCK_NAME)..."
    "$UV_EXE" pip install --python "$VENV_PYTHON" --require-hashes -r "$LEGACY_LOCK_PATH" || exit 2
fi

echo "Instalando wheel $WHEEL_NAME..."
"$UV_EXE" pip install --python "$VENV_PYTHON" --no-deps "$WHEEL_PATH" || exit 2

# 5. Executar traslado setup e promover somente se os gates aprovarem
set -- -I -m traslado setup \
    --release-id "$RELEASE_ID" \
    --profile "$NORM_PROFILE" \
    --languages "$LANGUAGES" \
    --root "$ROOT" \
    --expected-digests "$DIGESTS_PATH" \
    --promote

if [ -n "$EXTRAS" ]; then
    set -- "$@" --extras "$EXTRAS"
fi
if [ "$NO_SHORTCUT" -eq 1 ]; then
    set -- "$@" --no-shortcut
fi
if [ -n "$FROM_DIR" ]; then
    set -- "$@" --from-dir "$FROM_DIR"
fi
if [ "$ALLOW_OFFLINE_PROMOTION" -eq 1 ]; then
    set -- "$@" --allow-offline-promotion
fi
if [ "$SKIP_OCR" -eq 1 ]; then
    set -- "$@" --skip-ocr
fi
if [ -n "$OCR_CACHE" ]; then
    set -- "$@" --ocr-cache "$OCR_CACHE"
fi

echo "Executando verificação de gates e configuração (traslado setup)..."
if ! "$VENV_PYTHON" "$@"; then
    echo "Os gates de verificação reprovaram a instalação candidata; current.json não foi alterado." >&2
    exit 2
fi

echo "Instalação do Traslado $CLEAN_VERSION concluída e ativa em $ROOT."
exit 0
}
main "$@"
