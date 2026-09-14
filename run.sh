#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x .venv/bin/term-station ]; then
    printf '%s\n' "请先安装：python3 -m venv .venv && .venv/bin/python -m pip install -e ."
    exit 1
fi
exec .venv/bin/term-station "$@"
