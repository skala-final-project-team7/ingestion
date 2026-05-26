"""AtlassianSourceAdapter 단위 테스트 — vendored Data Ingestion Agent 통합 경계 검증.

vendored ``run_full_crawl_workflow`` 를 fake Confluence client 와 함께 실제로 구동해
ProcessedDocument→PageObject 매핑·PoC ACL 합성·since 필터·list_active_ids 를 검증한다.
외부 HTTP 는 fake client 로 대체한다(루트 CLAUDE.md 테스트 규칙).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.adapters.atlassian import AtlassianSourceAdapter
from app.adapters.json_fixture import parse_atlassian_datetime


class _FakeConfluenceClient:
    """vendored DataIngestionClient protocol 을 만족하는 in-memory fake."""

    def __init__(
        self,
        *,
        spaces: list[dict[str, Any]],
        descendants_by_homepage: dict[str, list[dict[str, Any]]],
        details_by_page: dict[str, dict[str, Any]],
    ) -> None:
        self.spaces = spaces
        self.descendants_by_homepage = descendants_by_homepage
        self.details_by_page = details_by_page

    def list_spaces(self) -> list[dict[str, Any]]:
        return self.spaces

    def list_page_descendants(self, homepage_id: str) -> list[dict[str, Any]]:
        return self.descendants_by_homepage.get(homepage_id, [])

    def get_page_detail(self, page_id: str) -> dict[str, Any]:
        return self.details_by_page[page_id]


def _space() -> dict[str, Any]:
    return {"id": "space-001", "key": "ENG", "name": "Engineering", "homepageId": "home-001"}


def _page_ref() -> dict[str, Any]:
    return {"id": "page-001", "parentId": "parent-001", "depth": 1, "position": 0}


def _page_detail() -> dict[str, Any]:
    return {
        "id": "page-001",
        "title": "Runbook",
        "status": "current",
        "body": {"storage": {"value": "<h1>Runbook</h1><p>Restart</p>"}},
        "createdAt": "2026-05-14T00:00:00Z",
        "version": {"number": 3, "createdAt": "2026-05-14T01:00:00Z"},
        "_links": {"webui": "/wiki/spaces/ENG/pages/page-001/Runbook"},
    }


def _adapter() -> AtlassianSourceAdapter:
    client = _FakeConfluenceClient(
        spaces=[_space()],
        descendants_by_homepage={"home-001": [_page_ref()]},
        details_by_page={"page-001": _page_detail()},
    )
    return AtlassianSourceAdapter(
        cloud_id="cloud-synthetic",
        access_token="token-synthetic",
        client=client,
        request_delay_seconds=0,
    )


def test_fetch_pages_maps_processed_document_to_page_object() -> None:
    pages = list(_adapter().fetch_pages())

    assert len(pages) == 1
    page = pages[0]
    assert page.page_id == "page-001"
    assert page.space_key == "ENG"
    assert page.title == "Runbook"
    assert "<h1>Runbook</h1>" in page.body_html
    assert page.version_number == 3
    assert page.webui_link == "/wiki/spaces/ENG/pages/page-001/Runbook"
    assert page.last_modified == parse_atlassian_datetime("2026-05-14T01:00:00Z")


def test_fetch_pages_synthesizes_space_acl_and_empty_mvp_fields() -> None:
    page = next(iter(_adapter().fetch_pages()))

    # PoC ACL 합성: space_key 기반 그룹. is_acl_missing 이 아니어야 색인 대상이 된다.
    assert page.allowed_groups == ["space:ENG"]
    assert page.allowed_users == []
    assert page.is_acl_missing is False
    # 에이전트 MVP 미산출 필드는 빈 값으로 매핑된다.
    assert page.labels == []
    assert page.ancestors == []
    assert page.attachments == []


def test_fetch_pages_since_filter_excludes_older_pages() -> None:
    future = datetime.fromisoformat("2030-01-01T00:00:00+00:00")
    assert list(_adapter().fetch_pages(since=future)) == []


def test_list_active_ids_returns_page_ids_without_attachments() -> None:
    ids = _adapter().list_active_ids()

    assert ids.pages == {"page-001"}
    assert ids.attachments == set()


def test_watch_changes_is_empty_stream() -> None:
    assert list(_adapter().watch_changes()) == []
