"""Data Ingestion Agent — Confluence Full Crawl (FR-001) [Agent 오케스트레이션].

--------------------------------------------------
작성목적 : vendored Data Ingestion Agent(저장소 루트 ``data_ingestion_agent``)를
          ``AtlassianSourceAdapter`` 로 감싸 Full Crawl 을 오케스트레이션한다. 수집한
          표준 PageObject 를 MongoDB ``raw_pages`` 에 적재하고 Chunking Queue
          (``content.chunking``)로 후속 메시지를 발행한 뒤, 잡 결과를 ``CrawlResult`` 로
          집계한다. 공급원 호출은 ``DocumentSourceAdapter`` 계약을 통해 추상화하며
          (vendored 직접 호출 금지), 적재·발행은 주입된 어댑터/스토어/publisher 를 통해
          수행한다(테스트는 fake 주입).
작성일 : 2026-05-26 (스캐폴드 stub → featureI-6 구현)
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-26, featureI-6, vendored Data Ingestion Agent 통합 — run_full_crawl 구현
    (어댑터 fetch_pages → raw_pages 적재 → Chunking Queue 발행 → CrawlResult 집계).
  - 2026-05-26, ADR 0003 항목 3, ``IngestionStage.CRAWL`` 추가에 따라 crawl 단계
    ``ingestion_jobs`` 기록 배선 — optional ``jobs`` 주입 시 페이지별 CRAWL SUCCESS 기록.
--------------------------------------------------
구현 메모(featureI-6):
  - 공급원 호출은 ``app/adapters/atlassian.py`` 의 ``AtlassianSourceAdapter`` 를 통해
    추상화한다(vendored ``run_full_crawl_workflow`` 를 블랙박스로 in-process 호출).
  - ``raw_store`` / ``publisher`` 는 외부 I/O 라 주입 가능하게 둔다. 운영에서는
    RabbitMQ 연결을 소유한 Ingestion Worker(featureI-2 후속)가 publisher 를 주입한다.
  - crawl 단계 ``ingestion_jobs`` 기록은 ``IngestionStage.CRAWL``(ADR 0003 항목 3, 공유
    enum — 양 레포 동시 갱신) 추가로 가능해졌다. ``jobs`` 를 주입하면 적재·발행에 성공한
    페이지마다 CRAWL SUCCESS 를 기록한다. 미주입(기본 None)이면 기존 동작 그대로
    ``CrawlResult`` 만 집계한다(비파괴). 실패 페이지는 status enum 에 적합한 코드가 없어
    ``failed_page_ids`` 로만 격리하고 잡 레코드는 남기지 않는다.
  - 토큰·자격증명은 로그·메시지 페이로드에 남기지 않는다(루트 CLAUDE.md 보안 규칙).
--------------------------------------------------
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.adapters.atlassian import AtlassianSourceAdapter
from app.adapters.base import DocumentSourceAdapter
from app.ingestion.workers import QUEUE_CHUNKING
from app.ingestion.workers.publisher import QueuePublisher
from app.schemas.enums import IngestionStage, IngestionStatus, SourceType
from app.schemas.page_object import PageObject
from app.storage.jobs import IngestionJobRecord, IngestionJobsRepository
from app.storage.raw_store import RawPageStore


@dataclass
class CrawlRequest:
    """Full Crawl 트리거 입력 (관리자 대시보드 / 스케줄러)."""

    space_key: str
    # PoC: BFF→Ingestion 전달(미확정 TBD). 로그·메시지 페이로드에 남기지 않는다.
    access_token: str | None = None
    cloud_id: str | None = None


@dataclass
class CrawlResult:
    """Full Crawl 잡 결과 리포트 (잡 리포트 — ingestion_jobs 대용, featureI-6 TBD)."""

    space_key: str
    pages_collected: int = 0
    attachments_collected: int = 0
    failed_page_ids: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def run_full_crawl(
    request: CrawlRequest,
    *,
    raw_store: RawPageStore,
    publisher: QueuePublisher,
    adapter: DocumentSourceAdapter | None = None,
    jobs: IngestionJobsRepository | None = None,
) -> CrawlResult:
    """Confluence Full Crawl 실행 (FR-001).

    흐름: 1) 어댑터로 vendored Full Crawl in-process 실행 → 2) 표준 PageObject 스트림
    수신 → 3) ``space_key`` 필터(트리거가 단일 스페이스일 때) → 4) ``raw_pages`` 적재 →
    5) Chunking Queue(``content.chunking``) 발행 → 6) ``CrawlResult`` 집계.

    Args:
        request: Full Crawl 트리거 입력(space_key + 주입 자격증명).
        raw_store: ``raw_pages`` 적재 어댑터(테스트는 ``FakeRawPageStore``).
        publisher: Chunking Queue 발행 publisher(테스트는 ``FakeQueuePublisher``).
            운영에서는 RabbitMQ 연결을 소유한 Worker 가 주입한다.
        adapter: 공급원 어댑터. None 이면 request 자격증명으로 ``AtlassianSourceAdapter``
            를 생성한다(vendored Data Ingestion Agent 호출).
        jobs: ``ingestion_jobs`` 적재 어댑터. 주입 시 적재·발행에 성공한 페이지마다
            ``IngestionStage.CRAWL`` SUCCESS 를 기록한다(ADR 0003 항목 3). None(기본)이면
            기록하지 않고 기존 동작대로 ``CrawlResult`` 만 집계한다(비파괴).

    Returns:
        수집·발행 결과를 집계한 ``CrawlResult``.

    Note:
        페이지 단위 적재·발행 실패는 잡 전체로 전파하지 않고 ``failed_page_ids`` 로 격리
        한다(graceful degrade). 첨부는 에이전트 MVP 미수집이라 항상 0 이다.
    """
    source = adapter or AtlassianSourceAdapter(
        cloud_id=request.cloud_id or "",
        access_token=request.access_token or "",
    )
    started = time.monotonic()
    result = CrawlResult(space_key=request.space_key)

    for page in source.fetch_pages():
        if request.space_key and page.space_key != request.space_key:
            continue
        page_started = datetime.now(UTC)
        try:
            raw_store.save_page(page)
            publisher.publish(
                routing_key=QUEUE_CHUNKING,
                message=build_chunking_message(page),
            )
        except Exception:  # noqa: BLE001 — 페이지 단위 격리(graceful degrade)
            result.failed_page_ids.append(page.page_id)
            continue
        result.pages_collected += 1
        if jobs is not None:
            jobs.record(
                IngestionJobRecord(
                    page_id=page.page_id,
                    attachment_id=None,
                    stage=IngestionStage.CRAWL,
                    status=IngestionStatus.SUCCESS,
                    started_at=page_started,
                    finished_at=datetime.now(UTC),
                    error=None,
                )
            )

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def build_chunking_message(page: PageObject) -> dict[str, object]:
    """Chunking Queue 발행 메시지 — 후속 Worker 가 ``page_id`` 로 raw_pages 를 조회한다.

    원본 본문·자격증명을 싣지 않고 식별자·멱등성 키만 전달한다(메시지 경량화 + 보안).
    Full Crawl(crawler) 과 Delta Sync(sync) 가 동일 메시지 형식을 공유한다.
    """
    return {
        "page_id": page.page_id,
        "space_key": page.space_key,
        "version_number": page.version_number,
        "source_type": SourceType.PAGE.value,
    }
