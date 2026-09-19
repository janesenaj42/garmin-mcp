#!/usr/bin/env bash
# Builds function.zip -- the file you upload in the Lambda console.
# Uses uv (https://docs.astral.sh/uv/) instead of a bare pip/zip so this
# works even without a pre-existing Python venv or a `zip` binary on PATH.
set -euo pipefail
rm -rf build function.zip
mkdir build
uv pip install -r requirements.txt --target build \
  --python-platform x86_64-manylinux2014 --python-version 3.12 --only-binary :all:
cp lambda_function.py build/
uv run --no-project python -c "
import shutil
shutil.make_archive('function', 'zip', 'build')
"
echo "Built function.zip -- upload this in the Lambda console."
