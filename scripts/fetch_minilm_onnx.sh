#!/usr/bin/env sh
# Download all-MiniLM-L6-v2 (ONNX + tokenizer) into application/api/models/.
# Needed when local.yaml bind-mounts the API tree over the image-baked copy.
#
# The revision and both SHA-256 values are read from the ARG lines in
# docker/Dockerfile, so the image build and this script always fetch the same
# files. Set MINILM_REVISION, MINILM_MODEL_SHA256 and MINILM_TOKENIZER_SHA256
# in the environment only to try a new revision before pinning it.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOCKERFILE="$ROOT/docker/Dockerfile"
DEST="${1:-$ROOT/application/api/models/all-MiniLM-L6-v2}"

arg_default() {
  sed -n "s/^ARG $1=//p" "$DOCKERFILE" | head -n 1
}

require_hex() {
  # require_hex NAME VALUE LENGTH
  case "$2" in
    "" | *[!0-9a-f]*)
      echo "$1 must be $3 lowercase hexadecimal characters; got '$2'" >&2
      exit 1
      ;;
  esac
  if [ "${#2}" -ne "$3" ]; then
    echo "$1 must be $3 lowercase hexadecimal characters; got '$2'" >&2
    exit 1
  fi
}

sha256_check() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c -
  else
    shasum -a 256 -c -
  fi
}

REVISION="${MINILM_REVISION:-$(arg_default MINILM_REVISION)}"
MODEL_SHA256="${MINILM_MODEL_SHA256:-$(arg_default MINILM_MODEL_SHA256)}"
TOKENIZER_SHA256="${MINILM_TOKENIZER_SHA256:-$(arg_default MINILM_TOKENIZER_SHA256)}"
require_hex MINILM_REVISION "$REVISION" 40
require_hex MINILM_MODEL_SHA256 "$MODEL_SHA256" 64
require_hex MINILM_TOKENIZER_SHA256 "$TOKENIZER_SHA256" 64

REPO="https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/$REVISION"

# Download next to the destination, verify, then move into place. A failed or
# interrupted run leaves the destination untouched.
mkdir -p "$(dirname "$DEST")"
TMP="$(mktemp -d "$DEST.tmp.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL --retry 3 "$REPO/onnx/model.onnx" -o "$TMP/model.onnx"
curl -fsSL --retry 3 "$REPO/tokenizer.json" -o "$TMP/tokenizer.json"
(
  cd "$TMP"
  printf '%s  model.onnx\n%s  tokenizer.json\n' "$MODEL_SHA256" "$TOKENIZER_SHA256" | sha256_check
)

mkdir -p "$DEST"
mv "$TMP/model.onnx" "$DEST/model.onnx"
mv "$TMP/tokenizer.json" "$DEST/tokenizer.json"
ls -l "$DEST"
