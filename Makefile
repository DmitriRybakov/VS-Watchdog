ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.PHONY: install lint typecheck test run migrate

install:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest --cov=watchdog --cov-report=term-missing

run:
	$(PY) -m uvicorn watchdog.web.app:app --reload --port 8000

migrate:
	$(PY) -m alembic upgrade head
