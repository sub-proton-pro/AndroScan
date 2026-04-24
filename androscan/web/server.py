"""Run uvicorn for the RE Workbench."""

from __future__ import annotations

import logging
from typing import Optional

import uvicorn

from androscan.config import Config
from androscan.web.app import create_app

logger = logging.getLogger(__name__)


def run_web_server(config: Config, port_override: Optional[int] = None, *, cwd: Optional[str] = None) -> None:
    """Block and serve the FastAPI app (reload disabled)."""
    from pathlib import Path

    cwd_path = Path(cwd) if cwd else None
    app = create_app(config, cwd=cwd_path)
    port = int(port_override) if port_override is not None else config.web_port
    host = config.web_host or "127.0.0.1"
    logger.info("Starting RE Workbench on http://%s:%s/", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)
