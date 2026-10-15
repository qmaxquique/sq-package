#!/usr/bin/env bash
# Stable checkout identity, without creating files or requiring host Python.
set -euo pipefail
export LC_ALL=C
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
    printf 'Usage: project-name.sh CHECKOUT_DIRECTORY\n' >&2
    exit 2
fi
source_root=$(cd -- "$1" && pwd -P)
slug=$(basename -- "$source_root" | tr -c 'A-Za-z0-9_.-' '-' | sed 's/^-*//;s/-*$//' | cut -c1-128)
slug=${slug:-project}
if command -v sha256sum >/dev/null 2>&1; then
    digest=$(printf '%s' "$source_root" | sha256sum)
elif command -v shasum >/dev/null 2>&1; then
    digest=$(printf '%s' "$source_root" | shasum -a 256)
elif command -v openssl >/dev/null 2>&1; then
    digest=$(printf '%s' "$source_root" | openssl dgst -sha256)
    digest=${digest##* }
else
    printf 'Install sha256sum, shasum, or openssl to calculate checkout identity.\n' >&2
    exit 1
fi
digest=${digest%% *}
printf 'ext-%s-%.12s\n' "$slug" "$digest"
