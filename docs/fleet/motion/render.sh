#!/usr/bin/env bash
# One command: ./render.sh [path/to/fleet.json]
# Default input: docs/demo/data/fleet.json, falling back to sample-fleet.json.
# Inlines the JSON into data.js (so the page works from file://), steps the GSAP
# timeline frame by frame in headless Chrome, and encodes fleet-risk.mp4 + poster.png.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
IN="${1:-$REPO/docs/demo/data/fleet.json}"
[ -f "$IN" ] || IN="$HERE/sample-fleet.json"
echo "render from: $IN"
python3 -I - "$IN" "$HERE/data.js" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
open(sys.argv[2], "w").write("window.FLEET = " + json.dumps(d) + ";\n")
print("sample" if d.get("sample") else "real")
PY
FRAMES="${FRAMES_DIR:-$(mktemp -d -t fleetframes)}"
export NODE_PATH="${PLAYWRIGHT_NODE_PATH:-/Users/wilsonwu/Downloads/workspace/node_modules}"
node "$HERE/render.js" "$HERE/index.html" "$FRAMES" "$HERE/poster.png"
ffmpeg -y -loglevel error -framerate 30 -i "$FRAMES/f%04d.png" -an -c:v libx264 -pix_fmt yuv420p -r 30 -crf 16 -movflags +faststart "$HERE/fleet-risk.mp4"
rm -rf "$FRAMES"
echo "wrote $HERE/fleet-risk.mp4 and $HERE/poster.png"
