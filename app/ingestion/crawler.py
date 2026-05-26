"""Data Ingestion Agent — Confluence Full Crawl (FR-001) [stub].

--------------------------------------------------
작성목적 : Atlassian REST API로 Confluence 계층을 초기 수집(Full Crawl)하고 적재 작업 큐를
          관리한다. 사용자가 접근 가능한 Space 목록 → homepageId → descendants 트리 순으로
          순회하며, 본문·메타·ACL(allowed_groups/allowed_users)·첨부(PDF/Word/Excel)를 수집해
          MongoDB(raw_pages / raw_attachments)에 적재하고 Chunking Queue로 메시지를 발행한다.
작성일 : 2026-05-26 (스캐폴드 — 인터페이스 정의, 구현은 featureI-2)
--------------------------------------------------
구현 메모(featureI-2):
  - 공급원 호출은 ``app/adapters`` 의 ``DocumentSourceAdapter`` 계약을 통해 추상화한다
    (Atlassian 어댑터 신규 구현 — ``app/adapters/atlassian.py``).
  - Rate Limit 고려 호출 속도 조절, 실패 페이지/첨부 재시도 또는 DLQ 보류.
  - 진행/결과는 ``app/storage`` 의 import_jobs 헬퍼로 기록한다.
--------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CrawlRequest:
    """Full Crawl 트리거 입력 (관리자 대시보드 / 스케줄러)."""

    space_key: str
    # PoC 3단계: BFF→Ingestion 전달. 로그·메시지 페이로드에 남기지 않는다.
    access_token: str | None = None
    cloud_id: str | None = None


@dataclass
class CrawlResult:
    """Full Crawl 잡 결과 리포트 (import_jobs 기록용)."""

    space_key: str
    pages_collected: int = 0
    attachments_collected: int = 0
    failed_page_ids: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def run_full_crawl(request: CrawlRequest) -> CrawlResult:
    """Confluence Full Crawl 실행 (FR-001).

    1) 접근 가능 Space 목록 조회 → 2) homepageId → 3) descendants 트리 순회 →
    4) Page 본문·메타·ACL 수집 → 5) 첨부 다운로드 → 6) raw_pages/raw_attachments 적재 →
    7) Chunking Queue 발행 → 8) 실패 재시도/DLQ.

    TODO(featureI-2): DocumentSourceAdapter(Atlassian) + Mongo 적재 + RabbitMQ 발행 구현.
    """
    raise NotImplementedError("featureI-2에서 구현 — docs/ai/current-plan.md 참조")
