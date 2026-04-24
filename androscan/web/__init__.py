"""Local RE Workbench (Phase 6): FastAPI + static React UI."""

from androscan.web.app import create_app
from androscan.web.server import run_web_server

__all__ = ["create_app", "run_web_server"]
