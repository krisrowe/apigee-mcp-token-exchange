VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: setup test studio run-gateway run-sts run-mcp-server bundle clean

setup: $(VENV)/bin/activate

$(VENV)/bin/activate: pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip --index-url https://pypi.org/simple
	$(PIP) install -e ".[dev]" --index-url https://pypi.org/simple
	touch $(VENV)/bin/activate

test: setup
	$(PYTHON) -m pytest -v tests/

studio: setup
	$(PYTHON) run_studio.py

bundle:
	@mkdir -p dist
	@zip -r dist/apigee-mcp-token-exchange.zip apiproxy
	@echo "Apigee proxy bundle created at dist/apigee-mcp-token-exchange.zip"

clean:
	rm -rf $(VENV) build/ dist/ *.egg-info .pytest_cache/
