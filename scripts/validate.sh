#!/usr/bin/env bash
# Run every check locally. This is the whole CI: there is no GitHub Actions.
#
#   ./scripts/validate.sh            # everything available
#   SKIP_HASSFEST=1 ./scripts/validate.sh   # skip the Docker-based check
#
# Exits non-zero if any check fails, so it also works as a git pre-push hook.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
ok() { printf '\033[1;32m   ok\033[0m\n'; }
bad() { printf '\033[1;31m   FAILED\033[0m\n'; fail=1; }

step "Lint (ruff: unused imports, syntax)"
if command -v uvx >/dev/null 2>&1; then
    uvx ruff check custom_components tests tests_ha --select F,E9 && ok || bad
else
    echo "   uvx not found; skipping"
fi

step "Library tests"
if command -v uv >/dev/null 2>&1; then
    uv run pytest -q && ok || bad
else
    python -m pytest -q && ok || bad
fi

step "Home Assistant integration tests"
if [ -x .venv-ha/bin/python ]; then
    .venv-ha/bin/python -m pytest -c pytest-ha.ini -q && ok || bad
else
    cat <<'EOF'
   .venv-ha not found; skipping. To enable:
     uv venv --python 3.13 .venv-ha
     uv pip install --python .venv-ha --prerelease=allow \
         pytest-homeassistant-custom-component numpy scipy shapely
EOF
fi

step "hassfest (Home Assistant manifest validation)"
if [ "${SKIP_HASSFEST:-0}" = "1" ]; then
    echo "   skipped (SKIP_HASSFEST=1)"
elif command -v docker >/dev/null 2>&1; then
    tmp="$(mktemp -d)"
    cp -r custom_components hacs.json "$tmp/"
    find "$tmp" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
    if docker run --rm -v "$tmp:/github/workspace" ghcr.io/home-assistant/hassfest:latest 2>&1 \
        | grep -q "Invalid integrations: 0"; then ok; else bad; fi
    rm -rf "$tmp"
else
    echo "   docker not found; skipping"
fi

step "Add-on image build"
if [ "${SKIP_DOCKER_BUILD:-0}" = "1" ]; then
    echo "   skipped (SKIP_DOCKER_BUILD=1)"
elif command -v docker >/dev/null 2>&1; then
    docker build -q -t spraying-control:local . >/dev/null && ok || bad
else
    echo "   docker not found; skipping"
fi

if [ "$fail" = "0" ]; then
    printf '\n\033[1;32mAll checks passed.\033[0m\n'
else
    printf '\n\033[1;31mSome checks failed.\033[0m\n'
fi
exit "$fail"
