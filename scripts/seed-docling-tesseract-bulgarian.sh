#!/usr/bin/env bash
# Seeds Tesseract's Bulgarian language weight into the docling-serve
# container's tessdata volume (docker-compose.yml's docling-tessdata
# named volume). The image ships Tesseract with only eng+osd -- without
# this file, ocr_preset="tesseract" with ocr_lang=["bul"] fails outright
# rather than misreading (Tesseract, unlike EasyOCR under the "auto"
# Docling preset, has no silent-wrong-script failure mode: it errors
# when the language file is absent).
#
# This is the second OCR engine for the cross-engine agreement check
# (crawler/render.py OCR_AGREEMENT step): EasyOCR and Tesseract fail in
# uncorrelated ways (measured 2026-08-27 -- EasyOCR: Cyrillic/Latin
# lookalike substitution; Tesseract: genuine misread real words), so
# requiring both to agree on a cell's text before shipping it turns two
# independently-unreliable readings into one much more trustworthy one,
# without ever guessing (a disagreement ships neither reading).
#
# The exact weight here (bul.traineddata, tessdata_fast) is the SAME
# file already benchmarked locally via `brew install tesseract-lang` --
# not a different/newer GitHub main-branch version -- so the container's
# Tesseract reproduces the measured behavior, not an unverified one.
#
# The weight lives in a named volume, not the image, so this must be
# re-run once per fresh volume (a new machine, `docker compose down -v`,
# or a volume prune) -- `docker compose up -d` alone is not enough.
#
# Usage: docker compose up -d && ./scripts/seed-docling-tesseract-bulgarian.sh
set -euo pipefail

CONTAINER="rfc_c_hybrid_crawler-docling-1"
MODEL_PATH="/usr/share/tesseract/tessdata/bul.traineddata"
URL="https://github.com/tesseract-ocr/tessdata_fast/raw/main/bul.traineddata"
EXPECTED_SHA256="aebc9b0fcc8cfaf8a9f38a02bb7b85052bd850744696a2c11cf0081820e5b21e"

if docker exec "$CONTAINER" test -f "$MODEL_PATH" 2>/dev/null; then
    echo "already seeded: $MODEL_PATH"
    exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading Tesseract Bulgarian weight..."
curl -sL --max-time 60 "$URL" -o "$tmp/bul.traineddata"

actual_sha256="$(shasum -a 256 "$tmp/bul.traineddata" 2>/dev/null | cut -d' ' -f1 \
    || sha256sum "$tmp/bul.traineddata" | cut -d' ' -f1)"
if [ "$actual_sha256" != "$EXPECTED_SHA256" ]; then
    echo "checksum mismatch: expected $EXPECTED_SHA256, got $actual_sha256" >&2
    exit 1
fi

docker cp "$tmp/bul.traineddata" "$CONTAINER:$MODEL_PATH"
echo "seeded: $MODEL_PATH (sha256 verified)"
