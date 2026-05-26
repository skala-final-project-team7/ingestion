#!/usr/bin/env python3
from __future__ import annotations

"""CLI entrypoint for Data Ingestion Agent full crawl."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_ingestion_agent.app.cli import main as cli_main


def main(argv: list[str] | None = None) -> int:
    """CLI script entrypoint."""
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
