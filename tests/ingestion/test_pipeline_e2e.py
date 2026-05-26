"""파이프라인 end-to-end PoC 테스트 (featureI-7) — crawl → raw_pages → 큐 → 색인 전 체인.

공급원 어댑터·임베더·Qdrant·Mongo·큐를 모두 Fake/in-memory 로 대체해, 두 단계로 분리된
파이프라인이 하나의 흐름으로 동작하는지 in-process 로 검증한다(멱등성·ACL 게이트 전파 포함).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from app.adapters.base import ActiveIds, ChangeEvent, DocumentSourceAdapter
from app.ingestion.crawler import CrawlRequest
from app.ingestion.pipeline import (
    build_poc_components,
    run_ingestion_pipeline,
    run_poc_ingestion,
)
from app.ingestion.vector_store import CONTENT_POOL, POOL_NAMES
from app.ingestion.workers.publisher import FakeQueuePublisher
from app.schemas.enums import IngestionStatus
from app.schemas.page_object import PageObject


def _page(page_id: str, *, acl: bool = True) -> PageObject:
    return PageObject(
        page_id=page_id,
        space_key="ENG",
        title="Runbook",
        body_html=(
            "<h2>Restart Procedure</h2>"
            "<p>Stop the service, clear the cache, then start it again and verify health.</p>"
        ),
        version_number=3,
        last_modified=datetime.fromisoformat("2026-05-14T01:00:00+00:00"),
        allowed_groups=["space:ENG"] if acl else [],
        allowed_users=[],
        webui_link=f"/wiki/{page_id}",
        labels=["operation"],
    )


class _FakeSource(DocumentSourceAdapter):
    def __init__(self, pages: list[PageObject]) -> None:
        self._pages = pages

    def fetch_pages(self, since: datetime | None = None) -> Iterator[PageObject]:
        yield from self._pages

    def list_active_ids(self) -> ActiveIds:
        return ActiveIds()

    def watch_changes(self) -> Iterator[ChangeEvent]:
        yield from ()


def test_end_to_end_crawl_to_qdrant_upsert() -> None:
    source = _FakeSource([_page("page-1"), _page("page-2")])

    result, components = run_poc_ingestion(CrawlRequest(space_key="ENG"), source)

    # crawl 단계: 두 페이지 수집·발행.
    assert result.crawl.pages_collected == 2
    # 색인 단계: 두 메시지 모두 SUCCESS.
    assert [r.status for r in result.indexed] == [IngestionStatus.SUCCESS, IngestionStatus.SUCCESS]
    assert all(r.upserted >= 1 for r in result.indexed)
    # Qdrant 적재: 세 Pool 모두에 동일 청크, page_id 가 scroll 로 보인다.
    for pool in POOL_NAMES:
        assert len(components.store.points[pool]) >= 2
    assert components.store.scroll_page_ids() == {"page-1", "page-2"}


def test_end_to_end_is_idempotent_on_rerun() -> None:
    components = build_poc_components()
    source = _FakeSource([_page("page-1")])
    request = CrawlRequest(space_key="ENG")

    first = run_ingestion_pipeline(
        request,
        source=source,
        raw_store=components.raw_store,
        publisher=FakeQueuePublisher(),
        chunking_deps=components.chunking_deps,
    )
    # 같은 raw_store·cache·store 를 공유한 재실행 — 캐시 히트로 재upsert 스킵.
    second = run_ingestion_pipeline(
        request,
        source=source,
        raw_store=components.raw_store,
        publisher=FakeQueuePublisher(),
        chunking_deps=components.chunking_deps,
    )

    assert first.indexed[0].upserted >= 1
    assert second.indexed[0].status is IngestionStatus.SUCCESS
    assert second.indexed[0].upserted == 0
    assert second.indexed[0].skipped == first.indexed[0].upserted


def test_end_to_end_acl_missing_page_not_indexed() -> None:
    source = _FakeSource([_page("page-ok"), _page("page-noacl", acl=False)])

    result, components = run_poc_ingestion(CrawlRequest(space_key="ENG"), source)

    statuses = {r.page_id: r.status for r in result.indexed}
    assert statuses["page-ok"] is IngestionStatus.SUCCESS
    assert statuses["page-noacl"] is IngestionStatus.INVALID_ACL
    # ACL 누락 페이지는 색인되지 않는다(전 체인 게이트 전파).
    assert components.store.scroll_page_ids() == {"page-ok"}
    assert all(
        point.page_id != "page-noacl" for point in components.store.points[CONTENT_POOL].values()
    )
