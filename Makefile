# Local workflow. There is no CI; these run on your machine.
.PHONY: help install test test-ha validate lint serve demo release

help:
	@echo "make install    install dependencies (uv)"
	@echo "make test       run the analysis library tests"
	@echo "make test-ha    run the Home Assistant integration tests"
	@echo "make validate   run every check (lint, tests, hassfest, image build)"
	@echo "make serve      run the web interface on :8099"
	@echo "make demo       print a demo analysis"
	@echo "make release VERSION=0.2.0   bump version, stamp changelog, tag"

install:
	uv sync

test:
	uv run pytest -q

test-ha:
	@test -x .venv-ha/bin/python || { \
	  echo "setting up .venv-ha (first run only)"; \
	  uv venv --python 3.13 .venv-ha; \
	  uv pip install --python .venv-ha --prerelease=allow \
	    pytest-homeassistant-custom-component numpy scipy shapely; }
	.venv-ha/bin/python -m pytest -c pytest-ha.ini -q

lint:
	uvx ruff check custom_components tests tests_ha --select F,E9

validate:
	./scripts/validate.sh

serve:
	uv run spraycontrol serve

demo:
	uv run spraycontrol demo

release:
	@test -n "$(VERSION)" || { echo "usage: make release VERSION=x.y.z"; exit 1; }
	./scripts/release.sh $(VERSION)
