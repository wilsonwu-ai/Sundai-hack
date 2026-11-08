"""Entry point, from the repo root: `uvicorn risk.api.server:app --port 4318`.

4318 is the OTLP/HTTP default port, so OTel exporters reach it with no config.
"""

import os

from dotenv import load_dotenv

from risk.api.app import create_app

load_dotenv()

app = create_app(os.environ.get("RISK_DB", "risk/data/monitor.sqlite"))
