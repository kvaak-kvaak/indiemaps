#!/usr/bin/env bash
# A/B pack library setup (local dev only — packs/ is gitignored).
#
# Seeds packs/ with the old-pipeline Southend build for side-by-side
# comparison against current demo data:
#   old:  pre-rewire Sep-18 demo (1703 POIs, stages base..ta, nodes-only OSM)
#   new:  live data/pois.json (served as demo)
# Source is git history (byte-exact, offline). Idempotent: skips packs
# already present with matching counts. Run once per machine/checkout.
#
#   scripts/pack/ab-packs.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/packs/eu/gb/england/essex/southend-old-pipeline"
EXPECT_TOTAL=1703
EXPECT_COMMIT=ce5030f

mkdir -p "$DEST"
if [ -f "$DEST/pois.json" ] \
  && [ "$(python3 -c "import json;print(len(json.load(open('$DEST/pois.json'))))")" = "$EXPECT_TOTAL" ]; then
  echo "old pack present ($EXPECT_TOTAL POIs), skipping extract"
else
  git -C "$ROOT" show "$EXPECT_COMMIT:data/pois.json" > "$DEST/pois.json"
  git -C "$ROOT" show "$EXPECT_COMMIT:data/build-meta.json" > "$DEST/meta.json"
  echo "extracted old pack from $EXPECT_COMMIT"
fi
python3 - "$DEST" "$EXPECT_TOTAL" <<'EOF'
import json, sys
dest, expect = sys.argv[1], int(sys.argv[2])
p = json.load(open(f'{dest}/pois.json'))
m = json.load(open(f'{dest}/meta.json'))
assert len(p) == expect, f'count {len(p)} != {expect} — aborting, investigate'
print(f"old pack OK: {len(p)} POIs, built {m.get('built_at')}, stages {m.get('stages_ok')}")
EOF
echo "select it in the map's pack menu (Southend old-pipeline entry)"
