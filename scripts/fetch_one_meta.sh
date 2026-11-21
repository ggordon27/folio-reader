#!/bin/sh
ID="$1"
if [ -z "$ID" ]; then exit 0; fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
gdrive readonly download "$ID" --out "/tmp/m_$ID.epub" >/dev/null 2>&1
if [ -f "/tmp/m_$ID.epub" ]; then
  python3 "$SCRIPT_DIR/extract_meta_helper.py" "$ID" "/tmp/m_$ID.epub"
  rm -f "/tmp/m_$ID.epub"
fi
