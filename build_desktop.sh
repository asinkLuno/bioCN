#!/usr/bin/env bash
# 打包桌面版（当前平台）为单目录可执行文件。
# 用法: ./build_desktop.sh
set -euo pipefail
cd "$(dirname "$0")"

NAME="biocn-gui"

echo "==> 开始打包 $NAME ..."

HOOKS="$(uv run python -c 'import os,kivy.tools.packaging.pyinstaller_hooks as p; print(os.path.dirname(p.__file__))')"
echo "==> Kivy hooks 目录: $HOOKS"

uv run pyinstaller \
    --noconfirm \
    --clean \
    --name "$NAME" \
    --path . \
    --additional-hooks-dir "$(pwd)/packaging/hooks" \
    --additional-hooks-dir "$HOOKS" \
    --collect-all hanlp \
    --collect-submodules kivy.core \
    --collect-submodules kivy.graphics \
    --hidden-import torch._dynamo \
    --exclude-module nvidia \
    --exclude-module triton \
    --exclude-module PyQt5 \
    desktop_entry.py

echo "==> 完成。产物在 dist/$NAME/ 下："
ls -la "dist/$NAME/"
