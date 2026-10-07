#!/bin/sh
# Cut a release: bump manifest.json to the given version, commit it, tag vX.Y.Z and push both.
# The tag push runs .github/workflows/release.yml, which publishes the GitHub release that
# HACS offers as an update in Home Assistant (Settings → Updates).
#
#   scripts/release.sh 0.7.0
#
# Only manifest.json is committed: commit the work being released first.
set -eu
cd "$(git rev-parse --show-toplevel)"

MANIFEST=custom_components/solar_smart_miner/manifest.json
NEW=${1:-}

if ! printf '%s' "$NEW" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "usage: scripts/release.sh X.Y.Z  (current: $(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])"))" >&2
    exit 1
fi
if [ "$(git rev-parse --abbrev-ref HEAD)" != main ]; then
    echo "release from main only" >&2
    exit 1
fi
if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then
    echo "tag v$NEW already exists" >&2
    exit 1
fi
if ! git diff --quiet -- "$MANIFEST" || ! git diff --cached --quiet; then
    echo "manifest.json or the index has uncommitted changes: commit them first" >&2
    exit 1
fi

OLD=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
if ! python3 -c "import sys;o,n=(tuple(map(int,v.split('.'))) for v in sys.argv[1:]);sys.exit(n<=o)" "$OLD" "$NEW"; then
    echo "new version $NEW must be above the current $OLD" >&2
    exit 1
fi

# Only the version line changes, so the rest of the file keeps its formatting.
python3 - "$MANIFEST" "$NEW" <<'EOF'
import json, re, sys
path, version = sys.argv[1:]
with open(path) as f:
    text = f.read()
text, count = re.subn(r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text)
if count != 1 or json.loads(text)["version"] != version:
    sys.exit(f"could not set the version in {path}")
with open(path, "w") as f:
    f.write(text)
EOF

git add "$MANIFEST"
git commit -m "chore(release): v$NEW"
git tag -a "v$NEW" -m "v$NEW"
git push origin main "v$NEW"
echo "Released v$NEW ($OLD → $NEW). GitHub Actions publishes the release; HACS shows it as an update."
