"""Entry point, from the repo root: `.venv/bin/uvicorn bank.api.server:app --port 8000`."""

from bank.api.app import create_app

app = create_app()
