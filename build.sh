#!/bin/sh
set -eu
cd "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if ! .venv/bin/python -c 'import PyInstaller' >/dev/null 2>&1; then
    printf '%s\n' "请先安装打包依赖：.venv/bin/python -m pip install -e '.[bundle]'"
    exit 1
fi
export PYINSTALLER_CONFIG_DIR="$PWD/build/pyinstaller-cache"
.venv/bin/python -m PyInstaller --noconfirm --clean term-station.spec
dist/term-station/term-station --version
printf '%s\n' "可执行文件：$PWD/dist/term-station/term-station"
printf '%s\n' "分发或安装时请保留整个 dist/term-station 目录（包含 _internal）。"
