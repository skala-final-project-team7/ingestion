#!/usr/bin/env python3
from __future__ import annotations

"""
--------------------------------------------------
작성자 : Codex
작성목적 : Data Sync Agent delta sync CLI 실행 스크립트.
          실제 CLI 구현은 package module에 두고 이 파일은 수동 실행 진입점으로만 유지한다.
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, feature7, workflow 실행 CLI module로 위임
--------------------------------------------------
[호환성]
  - Python 3.11.x 권장
  - argparse 기반 package CLI 위임
--------------------------------------------------
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_sync_agent.scripts.run_delta_sync import main


if __name__ == "__main__":
    raise SystemExit(main())
