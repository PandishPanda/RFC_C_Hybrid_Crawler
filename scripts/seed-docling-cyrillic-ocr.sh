#!/usr/bin/env bash
# Seeds the EasyOCR Cyrillic recognizer weight into the docling-serve
# container's model cache (docker-compose.yml's docling-models named
# volume). Without this file, docling's OCR silently misreads Cyrillic
# scanned PDFs as Latin lookalikes ("OBIIIECTBEHO 3IPABE" for
# "ОБЩЕСТВЕНО ЗДРАВЕ") -- no error, just wrong text. Every table-pdf
# source this crawler points at is a Bulgarian document, so
# crawler/render.py requests Bulgarian OCR (DOCLING_OCR_LANG) by
# default; this script is what makes that request satisfiable.
#
# The weight lives in a named volume, not the image, so this must be
# re-run once per fresh volume (a new machine, `docker compose down -v`,
# or a volume prune) -- `docker compose up -d` alone is not enough.
#
# Usage: docker compose up -d && ./scripts/seed-docling-cyrillic-ocr.sh
set -euo pipefail

CONTAINER="rfc_c_hybrid_crawler-docling-1"
MODEL_PATH="/opt/app-root/src/.cache/docling/models/EasyOcr/cyrillic_g2.pth"
URL="https://github.com/JaidedAI/EasyOCR/releases/download/v1.6.1/cyrillic_g2.zip"
EXPECTED_MD5="19f85f43d9128a89ac21b8d6a06973fe"

if docker exec "$CONTAINER" test -f "$MODEL_PATH" 2>/dev/null; then
    echo "already seeded: $MODEL_PATH"
    exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "downloading EasyOCR Cyrillic weight..."
curl -sL --max-time 60 "$URL" -o "$tmp/cyrillic_g2.zip"
unzip -q -o "$tmp/cyrillic_g2.zip" -d "$tmp"

actual_md5="$(md5sum "$tmp/cyrillic_g2.pth" 2>/dev/null | cut -d' ' -f1 \
    || md5 -q "$tmp/cyrillic_g2.pth")"
if [ "$actual_md5" != "$EXPECTED_MD5" ]; then
    echo "checksum mismatch: expected $EXPECTED_MD5, got $actual_md5" >&2
    exit 1
fi

docker cp "$tmp/cyrillic_g2.pth" "$CONTAINER:$MODEL_PATH"
echo "seeded: $MODEL_PATH (md5 verified)"
