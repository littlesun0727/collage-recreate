#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x demo/.editor-venv/bin/python ]; then
  EDITOR_BOOTSTRAP="${COLLAGE_PYTHON:-python3}"
  "$EDITOR_BOOTSTRAP" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "Python 3.11+ is required; set COLLAGE_PYTHON to a newer interpreter.")'
  "$EDITOR_BOOTSTRAP" -m venv demo/.editor-venv
fi
EDITOR_PYTHON=demo/.editor-venv/bin/python
"$EDITOR_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else "The editor virtual environment requires Python 3.11+.")'
if ! "$EDITOR_PYTHON" -c 'import PIL,numpy,cv2,onnxruntime,jsonschema,referencing,av' 2>/dev/null; then
  "$EDITOR_PYTHON" -m pip install -r skill/requirements-motion.txt
fi
exec "$EDITOR_PYTHON" demo/edit.py "$@"
