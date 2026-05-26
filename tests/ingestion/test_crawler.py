"""run_full_crawl 단위 테스트 — 어댑터→raw_pages 적재→Chunking Queue 발행 흐름 검증.

공급원 어댑터·raw_store·publisher 를 모두 fake 로 주입해 crawler 오케스트레이션만
검증한다(외부 의존성 mock — 루트 CLAUDE.md 테스트 규칙).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from app.adapters.base import ActiveIds, ChangeEvent, DocumentSourceAdapter
from app.ingestion.crawler import CrawlRequest, run_full_crawl
from app.ingestion.workers import QUEUE_CHUNKING
from app.ingestion.workers.publisher import FakeQueuePublisher
from app.schemas.page_object import PageObject
from app.storage.raw_store import FakeRawPageStore


def _page(page_id: str, *, space_key: str = "ENG", version: int = 1) -> PageObject:
    return PageObject(
        page_id=page_id,
        space_key=space_key,
        title=f"Title {page_id}",
        body_html=f"<p>{page_id}</p>",
        version_number=version,
        last_modified=datetime.fromisoformat("2026-05-14T01:00:00+00:00"),
        allowed_groups=[f"space:{space_key}"],
        allowed_users=[],
        webui_link=f"/wiki/{page_id}",
    )


class _FakeSource(DocumentSourceAdapter):
    """미리 만든 PageObject 를 그대로 yield 하는 fake 공급원."""

    def __init__(self, pages: list[PageObject]) -> None:
        self._pages = pages

    def fetch_pages(self, since: datetime | None = None) -> Iterator[PageObject]:
        yield from self._pages

    def list_active_ids(self) -> ActiveIds:
        return ActiveIds()

    def watch_changes(self) -> Iterator[ChangeEvent]:
        yield from ()


def test_run_full_crawl_persists_pages_and_publishes_chunking_messages() -> None:
    store = FakeRawPageStore()
    publisher = FakeQueuePublisher()
    source = _FakeSource([_page("page-1", version=2), _page("page-2", version=5)])

    result = run_full_crawl(
        CrawlRequest(space_key="ENG"),
        raw_store=store,
        publisher=publisher,
        adapter=source,
    )

    assert result.pages_collected == 2
    assert result.attachments_collected == 0
    assert result.failed_page_ids == []
    assert set(store.pages) == {"page-1", "page-2"}

    assert [m.routing_key for m in publisher.messages] == [QUEUE_CHUNKING, QUEUE_CHUNKING]
    first = publisher.messages[0].body
    assert first == {
        "page_id": "page-1",
        "space_key": "ENG",
        "version_number": 2,
        "source_type": "page",
    }


def test_run_full_crawl_filters_by_requested_space_key() -> None:
    store = FakeRawPageStore()
    publisher = FakeQueuePublisher()
    source = _FakeSource([_page("page-1", space_key="ENG"), _page("page-2", space_key="OPS")])

    result = run_full_crawl(
        CrawlRequest(space_key="ENG"),
        raw_store=store,
        publisher=publisher,
        adapter=source,
    )

    assert result.pages_collected == 1
    assert set(store.pages) == {"page-1"}
    assert len(publisher.messages) == 1


def test_run_full_crawl_isolates_failed_page() -> None:
    class _RaisingStore(FakeRawPageStore):
        def save_page(self, page: PageObject) -> None:
            if page.page_id == "page-bad":
                raise RuntimeError("mongo down")
            super().save_page(page)

    store = _RaisingStore()
    publisher = FakeQueuePublisher()
    source = _FakeSource([_page("page-ok"), _page("page-bad")])

    result = run_full_crawl(
        CrawlRequest(space_key="ENG"),
        raw_store=store,
        publisher=publisher,
        adapter=source,
    )

    assert result.pages_collected == 1
    assert result.failed_page_ids == ["page-bad"]
    assert set(store.pages) == {"page-ok"}
