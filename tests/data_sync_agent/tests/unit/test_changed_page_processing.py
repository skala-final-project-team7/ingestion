from __future__ import annotations

from uuid import uuid4

import pytest

from data_sync_agent.confluence import ConfluenceApiError
from data_sync_agent.schemas import ChangeType, PageSnapshot, PageSnapshotItem
from data_sync_agent.sync.diff_engine import PageChange, diff_snapshots
from data_sync_agent.sync.changed_page_processor import (
    ChangedPageProcessingResult,
    ChangedPageProcessor,
    extract_storage_html,
)


class FakePageDetailClient:
    def __init__(self, details: dict[str, dict] | None = None) -> None:
        self.details = details or {}
        self.requested_page_ids: list[str] = []

    def get_page_detail(self, page_id: str) -> dict:
        self.requested_page_ids.append(page_id)
        detail = self.details[page_id]
        if isinstance(detail, Exception):
            raise detail
        return detail


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _page(
    *,
    cloud_id: str,
    page_id: str,
    space_id: str = "space-1",
    version_number: int = 1,
    last_modified_at: str = "2026-05-15T00:00:00Z",
) -> PageSnapshotItem:
    return PageSnapshotItem(
        cloud_id=cloud_id,
        space_id=space_id,
        space_key="ENG",
        space_name="Engineering",
        page_id=page_id,
        title=f"Synthetic {page_id}",
        status="current",
        page_url=f"https://example.invalid/wiki/spaces/ENG/pages/{page_id}",
        last_modified_at=last_modified_at,
        version_number=version_number,
    )


def _snapshot(
    *,
    cloud_id: str,
    pages: list[PageSnapshotItem],
    suffix: str,
) -> PageSnapshot:
    return PageSnapshot(
        snapshot_id=f"snapshot-{suffix}",
        sync_id=f"sync-{suffix}",
        cloud_id=cloud_id,
        created_at="2026-05-15T00:30:00Z",
        pages=pages,
    )


def _detail(
    *,
    page_id: str,
    version_number: int,
    html: str,
) -> dict:
    return {
        "id": page_id,
        "title": f"Synthetic {page_id}",
        "status": "current",
        "_links": {"webui": f"https://example.invalid/wiki/pages/{page_id}"},
        "version": {
            "number": version_number,
            "createdAt": "2026-05-15T01:00:00Z",
        },
        "body": {"storage": {"value": html, "representation": "storage"}},
    }


def _mixed_diff() -> tuple[str, list[PageChange]]:
    cloud_id = _runtime_value("cloud")
    previous = _snapshot(
        cloud_id=cloud_id,
        pages=[
            _page(cloud_id=cloud_id, page_id="updated", version_number=1),
            _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            _page(cloud_id=cloud_id, page_id="deleted", version_number=1),
        ],
        suffix="previous",
    )
    current = _snapshot(
        cloud_id=cloud_id,
        pages=[
            _page(cloud_id=cloud_id, page_id="new", version_number=1),
            _page(cloud_id=cloud_id, page_id="updated", version_number=2),
            _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
        ],
        suffix="current",
    )
    diff_result = diff_snapshots(previous, current)
    return cloud_id, (
        diff_result.changed_pages
        + diff_result.unchanged_pages
        + diff_result.deleted_candidates
    )


def test_only_new_and_updated_pages_are_fetched() -> None:
    cloud_id, page_changes = _mixed_diff()
    client = FakePageDetailClient(
        {
            "new": _detail(page_id="new", version_number=1, html="<p>new</p>"),
            "updated": _detail(
                page_id="updated",
                version_number=2,
                html="<p>updated</p>",
            ),
        }
    )
    processor = ChangedPageProcessor(client=client)

    result = processor.process(
        page_changes,
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    assert isinstance(result, ChangedPageProcessingResult)
    assert client.requested_page_ids == ["new", "updated"]
    assert [document.change_type for document in result.changed_documents] == [
        ChangeType.NEW,
        ChangeType.UPDATED,
    ]


def test_storage_html_and_plain_text_are_preserved() -> None:
    cloud_id = _runtime_value("cloud")
    page = _page(cloud_id=cloud_id, page_id="page-1")
    html = """
    <h1>Heading</h1>
    <p>Paragraph with <a href="https://example.invalid">Link Text</a></p>
    <ul><li>First item</li><li>Second item</li></ul>
    <table><tr><td>Cell A</td><td>Cell B</td></tr></table>
    """
    client = FakePageDetailClient(
        {"page-1": _detail(page_id="page-1", version_number=1, html=html)}
    )
    processor = ChangedPageProcessor(client=client)

    result = processor.process(
        [
            PageChange(
                change_type=ChangeType.NEW,
                page_key=str(page.page_key),
                previous=None,
                current=page,
            )
        ],
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    document = result.changed_documents[0].to_dict()

    assert document["body"]["storage_html"] == html
    plain_text = document["body"]["plain_text"]
    assert "Heading" in plain_text
    assert "Paragraph with Link Text" in plain_text
    assert "First item" in plain_text
    assert "Second item" in plain_text
    assert "Cell A" in plain_text
    assert "Cell B" in plain_text


def test_malformed_html_does_not_fail_processing() -> None:
    html = "<h1>Heading<p>Broken <strong>HTML"

    extraction = extract_storage_html(html)

    assert extraction.storage_html == html
    assert "Heading" in extraction.plain_text
    assert "Broken HTML" in extraction.plain_text


def test_attachment_and_macro_are_marked_not_supported_without_failure() -> None:
    cloud_id = _runtime_value("cloud")
    page = _page(cloud_id=cloud_id, page_id="page-1")
    html = (
        "<ac:structured-macro ac:name=\"toc\">Macro Title</ac:structured-macro>"
        "<ri:attachment ri:filename=\"synthetic.pdf\" />"
        "<p>Visible text</p>"
    )
    client = FakePageDetailClient(
        {"page-1": _detail(page_id="page-1", version_number=1, html=html)}
    )

    result = ChangedPageProcessor(client=client).process(
        [
            PageChange(
                change_type=ChangeType.UPDATED,
                page_key=str(page.page_key),
                previous=page,
                current=page,
            )
        ],
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    document = result.changed_documents[0].to_dict()

    assert document["metadata"]["attachment_processing_status"] == (
        "not_supported_in_mvp"
    )
    assert document["metadata"]["has_unsupported_content"] is True
    assert "Visible text" in document["body"]["plain_text"]


def test_partial_page_detail_failure_records_failed_item_and_continues() -> None:
    cloud_id = _runtime_value("cloud")
    success_page = _page(cloud_id=cloud_id, page_id="success")
    failed_page = _page(cloud_id=cloud_id, page_id="failed")
    client = FakePageDetailClient(
        {
            "success": _detail(page_id="success", version_number=1, html="<p>ok</p>"),
            "failed": ConfluenceApiError(
                status_code=403,
                error_type="permission_failure",
                message="denied Authorization",
                retryable=False,
                item_level=True,
                attempt_count=1,
            ),
        }
    )

    result = ChangedPageProcessor(client=client).process(
        [
            PageChange(
                change_type=ChangeType.NEW,
                page_key=str(success_page.page_key),
                previous=None,
                current=success_page,
            ),
            PageChange(
                change_type=ChangeType.UPDATED,
                page_key=str(failed_page.page_key),
                previous=failed_page,
                current=failed_page,
            ),
        ],
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    assert [document.page["page_id"] for document in result.changed_documents] == [
        "success"
    ]
    assert len(result.failed_items) == 1
    failed_item = result.failed_items[0].to_dict()
    assert failed_item["stage"] == "fetch_page_detail"
    assert failed_item["item_id"] == "failed"
    assert "Authorization" not in failed_item["error_message"]


def test_changed_document_metadata_contains_required_fields() -> None:
    cloud_id = _runtime_value("cloud")
    page = _page(cloud_id=cloud_id, page_id="page-1", version_number=3)
    client = FakePageDetailClient(
        {"page-1": _detail(page_id="page-1", version_number=3, html="<p>body</p>")}
    )

    result = ChangedPageProcessor(client=client).process(
        [
            PageChange(
                change_type=ChangeType.NEW,
                page_key=str(page.page_key),
                previous=None,
                current=page,
            )
        ],
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    document = result.changed_documents[0].to_dict()

    assert document["document_id"] == "confluence-page-page-1-3"
    assert document["source_type"] == "confluence_page"
    assert document["page"]["page_id"] == "page-1"
    assert document["page"]["space_id"] == "space-1"
    assert document["page"]["version_number"] == 3
    assert document["page"]["title"] == "Synthetic page-1"
    assert document["page"]["page_url"] == "https://example.invalid/wiki/pages/page-1"
    assert document["metadata"]["detected_at"] == "2026-05-15T02:00:00Z"


def test_changed_outputs_do_not_include_sensitive_values() -> None:
    cloud_id = _runtime_value("cloud")
    sensitive_value = _runtime_value("runtime-token")
    secret_like_value = _runtime_value("secret-like")
    page = _page(cloud_id=cloud_id, page_id="page-1")
    client = FakePageDetailClient(
        {"page-1": _detail(page_id="page-1", version_number=1, html="<p>safe</p>")}
    )

    result = ChangedPageProcessor(client=client).process(
        [
            PageChange(
                change_type=ChangeType.NEW,
                page_key=str(page.page_key),
                previous=None,
                current=page,
            )
        ],
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    serialized = str([document.to_dict() for document in result.changed_documents])
    serialized += str([failed_item.to_dict() for failed_item in result.failed_items])

    assert sensitive_value not in serialized
    assert secret_like_value not in serialized
    assert "access_token" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
