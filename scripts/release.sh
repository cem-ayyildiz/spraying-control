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

# Pull this version's section out of the CHANGELOG to use as the release notes.
# Kept in .git/ (not a temp dir) so the printed command still works later.
notes_file=".git/RELEASE_NOTES_v$version.md"
awk -v v="$version" '
    $0 ~ "^## \\[" v "\\]" { grab=1; next }
    grab && /^## \[/ { exit }
    grab { print }
' CHANGELOG.md > "$notes_file"

echo
echo "Tagged v$version. Now push, then publish the GitHub Release."
echo "HACS lists GitHub Releases, not bare tags, so the release is what makes"
echo "the update show up in Home Assistant."
echo
echo "  git push origin main"
echo "  git push origin v$version"

if command -v gh >/dev/null 2>&1; then
    printf '\nPublish the release now with gh? [y/N] '
    read -r ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        git push origin main
        git push origin "v$version"
        gh release create "v$version" --verify-tag --latest \
            --title "v$version" --notes-file "$notes_file"
        echo "Published https://github.com/cem-ayyildiz/spraying-control/releases/tag/v$version"
        rm -f "$notes_file"
    else
        echo
        echo "When ready:"
        echo "  gh release create v$version --verify-tag --latest --title v$version --notes-file $notes_file"
    fi
else
    echo
    echo "Then create a Release from the tag on GitHub (Releases -> Draft a new release)."
fi
