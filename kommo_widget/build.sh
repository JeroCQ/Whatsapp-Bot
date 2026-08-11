#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIDGET="$ROOT/kommo_widget"
OUTPUT="$ROOT/dist/kommo-salesbot-widget.zip"

python3 "$WIDGET/generate_images.py"
python3 -m json.tool "$WIDGET/manifest.json" >/dev/null
python3 -m json.tool "$WIDGET/i18n/es.json" >/dev/null
python3 -m json.tool "$WIDGET/i18n/en.json" >/dev/null

mkdir -p "$ROOT/dist"
rm -f "$OUTPUT"
(
  cd "$WIDGET"
  zip -q -r "$OUTPUT" manifest.json script.js i18n images
)

python3 - "$OUTPUT" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    names = archive.namelist()
    assert "manifest.json" in names, "manifest.json must be at the ZIP root"
    assert "kommo_widget/manifest.json" not in names, "do not wrap files in another directory"
    assert all(not name.startswith("/") for name in names)
print(f"Built {sys.argv[1]} ({len(names)} entries)")
PY
