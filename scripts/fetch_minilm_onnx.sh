#!/usr/bin/env sh
# Download all-MiniLM-L6-v2 (ONNX + tokenizer) into models/ for the api embedder.
set -eu
DEST="${1:-$(dirname "$0")/../models/all-MiniLM-L6-v2}"
REPO="https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main"
mkdir -p "$DEST"
curl -fsSL "$REPO/onnx/model.onnx" -o "$DEST/model.onnx"
curl -fsSL "$REPO/tokenizer.json" -o "$DEST/tokenizer.json"
ls -l "$DEST"
