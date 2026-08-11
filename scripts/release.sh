#!/usr/bin/env bash
# Cut a release, entirely on your machine.
#
#   ./scripts/release.sh 0.2.0
#
# Keeps the version in sync across the three files that carry it, stamps the
# CHANGELOG, runs the local checks, then commits and tags. Push is left to you:
#   git push origin main && git push origin v<version>
set -euo pipefail
cd "$(dirname "$0")/.."

version="${1:-}"
if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "usage: $0 <major.minor.patch>   e.g. $0 0.2.0" >&2
    exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
    echo "working tree is not clean; commit or stash first" >&2
    exit 1
fi

echo "Setting version $version in the three manifests..."
# pyproject.toml: the first version line under [project]
python3 - "$version" <<'PY'
import re, sys, pathlib
v = sys.argv[1]
p = pathlib.Path("pyproject.toml")
p.write_text(re.sub(r'(?m)^version = ".*"$', f'version = "{v}"', p.read_text(), count=1))
p = pathlib.Path("config.yaml")
p.write_text(re.sub(r'(?m)^version: ".*"$', f'version: "{v}"', p.read_text(), count=1))
import json
mp = pathlib.Path("custom_components/spraying_control/manifest.json")
m = json.loads(mp.read_text())
m["version"] = v
mp.write_text(json.dumps(m, indent=2) + "\n")
print("  pyproject.toml, config.yaml, manifest.json ->", v)
PY

# Stamp the CHANGELOG: turn "## [Unreleased]" into the released version.
if grep -q "## \[Unreleased\]" CHANGELOG.md; then
    today="$(date +%Y-%m-%d)"
    python3 - "$version" "$today" <<'PY'
import sys, pathlib
v, today = sys.argv[1], sys.argv[2]
p = pathlib.Path("CHANGELOG.md")
text = p.read_text()
text = text.replace(
    "## [Unreleased]",
    f"## [Unreleased]\n\n## [{v}] - {today}",
    1,
)
p.write_text(text)
print(f"  CHANGELOG.md stamped {v} ({today})")
PY
else
    echo "  no '## [Unreleased]' section in CHANGELOG.md; edit it by hand" >&2
fi

echo "Running local checks..."
./scripts/validate.sh

git add pyproject.toml config.yaml custom_components/spraying_control/manifest.json CHANGELOG.md
git commit -m "release: v$version"
git tag -a "v$version" -m "v$version"

cat <<EOF

Tagged v$version. To publish:
  git push origin main
  git push origin v$version
EOF
