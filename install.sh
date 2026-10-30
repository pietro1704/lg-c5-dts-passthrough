#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Erro: Python 3 precisa estar instalado na TV.' >&2
    exit 1
fi
exec python3 -B "$script_dir/tv-package.py" install "$@"
