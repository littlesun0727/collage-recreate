#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x demo/.editor-venv/bin/python ]; then
  python3 -m venv demo/.editor-venv
fi
PYTHON=demo/.editor-venv/bin/python
if ! "$PYTHON" -c 'import PIL,numpy,cv2,onnxruntime,jsonschema,referencing,av' 2>/dev/null; then
  "$PYTHON" -m pip install -r skill/requirements-motion.txt
fi
exec "$PYTHON" demo/edit.py "$@"
