"""Entry point, from the repo root: `uvicorn risk.api.server:app --port 4318`.

4318 is the OTLP/HTTP default port, so OTel exporters reach it with no config.
The detector is BehaviorGPT when UNBOXAI_API_KEY and UNBOXAI_CATALOG_ID are set
(in .env), else the severity-only baseline; GET /healthz says which.
"""

import os

from dotenv import load_dotenv

from risk.api.app import create_app
from risk.scoring.surprise_severity import detector_from_env

load_dotenv()

app = create_app(
    os.environ.get("RISK_DB", "risk/data/monitor.sqlite"),
    detector=detector_from_env(),
)
