#!/usr/bin/env bash
#
# Convert a PDF (local file or URL) via the local docling-serve container and
# dump every table as TSV, so curriculum grids can be read without opening the
# 1 MB DoclingDocument JSON by hand.
#
#   scripts/docling-convert.sh <pdf-path-or-url> [-o outdir] [-- extra -F fields]
#
# Examples:
#   scripts/docling-convert.sh ~/Downloads/plan.pdf
#   scripts/docling-convert.sh https://example.bg/plan.pdf -o /tmp/out
#   scripts/docling-convert.sh plan.pdf -- -F 'do_ocr=false' -F 'table_mode=fast'
#
# Requires the docling service: docker compose --profile docling up -d docling
set -euo pipefail

DOCLING_URL="${DOCLING_SERVE_URL:-http://localhost:5001}"
OUTDIR="docling-out"
SRC=""
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o | --out)
      OUTDIR="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --)
      shift
      EXTRA=("$@")
      break
      ;;
    *)
      if [[ -n "$SRC" ]]; then
        echo "error: unexpected argument '$1' (source already set to '$SRC')" >&2
        exit 2
      fi
      SRC="$1"
      shift
      ;;
  esac
done

if [[ -z "$SRC" ]]; then
  echo "usage: $(basename "$0") <pdf-path-or-url> [-o outdir] [-- extra -F fields]" >&2
  exit 2
fi

if ! curl -fsS --max-time 5 "$DOCLING_URL/health" >/dev/null 2>&1; then
  echo "error: docling-serve is not responding at $DOCLING_URL" >&2
  echo "       start it with: docker compose --profile docling up -d docling" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$OUTDIR"

# Name outputs after the source, minus directory and extension.
base="$(basename "$SRC")"
base="${base%.[Pp][Dd][Ff]}"
raw="$OUTDIR/$base.json"

if [[ "$SRC" =~ ^https?:// ]]; then
  # v1.28 takes a unified `sources` array; older docs show http_sources (422s here).
  payload=$(SRC="$SRC" python3 -c '
import json, os
print(json.dumps({"sources": [{"kind": "http", "url": os.environ["SRC"]}],
                  "options": {"to_formats": ["md", "json"]}}))')
  echo "converting (url): $SRC"
  curl -fsS -X POST "$DOCLING_URL/v1/convert/source" \
    -H 'Content-Type: application/json' -d "$payload" -o "$raw"
else
  if [[ ! -f "$SRC" ]]; then
    echo "error: no such file: $SRC" >&2
    exit 1
  fi
  echo "converting (file): $SRC"
  curl -fsS -X POST "$DOCLING_URL/v1/convert/file" \
    -F "files=@$SRC;type=application/pdf" \
    -F 'to_formats=md' -F 'to_formats=json' \
    "${EXTRA[@]+"${EXTRA[@]}"}" -o "$raw"
fi

exec python3 "$SCRIPT_DIR/docling-tables.py" "$raw" "$OUTDIR" "$base"
