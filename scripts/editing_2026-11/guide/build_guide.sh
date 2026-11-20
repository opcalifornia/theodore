#!/bin/bash
# Build the Theodore field guide PDF with Chrome's print engine (separate profile, does not touch your Chrome).
cd "$(dirname "$0")"
OUT="$HOME/Documents/Theodore/Guide"; mkdir -p "$OUT"
cp -R assets "$OUT/" ; cp theodore_guide.html "$OUT/Theodore Field Guide.html"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-pdf-header-footer \
  --user-data-dir="/tmp/theodore-guide-profile" --print-to-pdf="$OUT/Theodore Field Guide.pdf" \
  "file://$OUT/Theodore Field Guide.html" 2>&1 | tail -1
ls -la "$OUT"
# keep the Desktop copy (Desktop/theodore/menu.pdf) in step with every rebuild
mkdir -p "$HOME/Desktop/theodore" && cp "$OUT/Theodore Field Guide.pdf" "$HOME/Desktop/theodore/menu.pdf"
