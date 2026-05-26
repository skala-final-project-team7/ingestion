from __future__ import annotations

from uuid import uuid4

import pytest

from data_sync_agent.schemas import ChangeType, PageSnapshot, PageSnapshotItem
from data_sync_agent.sync.diff_engine import (
    DiffEngineError,
    PageChange,
    diff_snapshots,
    index_snapshot_pages,
)


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _page(
    *,
    cloud_id: str,
    space_id: str = "space-1",
    page_id: str = "page-1",
    version_number: int = 1,
    last_modified_at: str = "2026-05-15T00:00:00Z",
) -> PageSnapshotItem:
    return PageSnapshotItem(
        cloud_id=cloud_id,
        space_id=space_id,
        space_key=f"KEY-{space_id}",
        space_name=f"Space {space_id}",
        page_id=page_id,
        title=f"Synthetic {page_id}",
        status="current",
        page_url=f"https://example.invalid/{space_id}/{page_id}",
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


def test_empty_previous_marks_all_current_pages_as_new() -> None:
    cloud_id = _runtime_value("cloud")
    current_pages = [
        _page(cloud_id=cloud_id, page_id="page-b"),
        _page(cloud_id=cloud_id, page_id="page-a"),
    ]

    diff_result = diff_snapshots(
        previous=_snapshot(cloud_id=cloud_id, pages=[], suffix="previous"),
        current=_snapshot(cloud_id=cloud_id, pages=current_pages, suffix="current"),
    )

    assert [change.change_type for change in diff_result.changed_pages] == [
        ChangeType.NEW,
        ChangeType.NEW,
    ]
    assert [change.current.page_id for change in diff_result.changed_pages] == [
        "page-a",
        "page-b",
    ]
    assert diff_result.summary.new_pages == 2
    assert diff_result.summary.deleted_candidates == 0


def test_empty_current_marks_all_previous_pages_as_deleted_candidates() -> None:
    cloud_id = _runtime_value("cloud")
    previous_pages = [
        _page(cloud_id=cloud_id, page_id="page-b"),
        _page(cloud_id=cloud_id, page_id="page-a"),
    ]

    diff_result = diff_snapshots(
        previous=_snapshot(cloud_id=cloud_id, pages=previous_pages, suffix="previous"),
        current=_snapshot(cloud_id=cloud_id, pages=[], suffix="current"),
    )

    assert [change.change_type for change in diff_result.deleted_candidates] == [
        ChangeType.DELETED_CANDIDATE,
        ChangeType.DELETED_CANDIDATE,
    ]
    assert [change.previous.page_id for change in diff_result.deleted_candidates] == [
        "page-a",
        "page-b",
    ]
    assert diff_result.summary.deleted_candidates == 2
    assert diff_result.changed_pages == []


def test_version_number_increase_marks_page_as_updated() -> None:
    cloud_id = _runtime_value("cloud")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[_page(cloud_id=cloud_id, version_number=1)],
            suffix="previous",
        ),
        current=_snapshot(
            cloud_id=cloud_id,
            pages=[_page(cloud_id=cloud_id, version_number=2)],
            suffix="current",
        ),
    )

    assert len(diff_result.changed_pages) == 1
    assert diff_result.changed_pages[0].change_type == ChangeType.UPDATED
    assert diff_result.summary.updated_pages == 1


def test_timestamp_change_marks_page_as_updated_when_version_is_same() -> None:
    cloud_id = _runtime_value("cloud")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(
                    cloud_id=cloud_id,
                    version_number=2,
                    last_modified_at="2026-05-15T00:00:00Z",
                )
            ],
            suffix="previous",
        ),
        current=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(
                    cloud_id=cloud_id,
                    version_number=2,
                    last_modified_at="2026-05-15T01:00:00Z",
                )
            ],
            suffix="current",
        ),
    )

    assert diff_result.changed_pages[0].change_type == ChangeType.UPDATED
    assert diff_result.summary.updated_pages == 1


def test_same_version_and_timestamp_marks_page_as_unchanged() -> None:
    cloud_id = _runtime_value("cloud")
    previous_page = _page(cloud_id=cloud_id)
    current_page = _page(cloud_id=cloud_id)

    diff_result = diff_snapshots(
        previous=_snapshot(cloud_id=cloud_id, pages=[previous_page], suffix="previous"),
        current=_snapshot(cloud_id=cloud_id, pages=[current_page], suffix="current"),
    )

    assert diff_result.changed_pages == []
    assert [change.change_type for change in diff_result.unchanged_pages] == [
        ChangeType.UNCHANGED
    ]
    assert diff_result.summary.unchanged_pages == 1


def test_same_page_id_in_different_space_is_different_page() -> None:
    cloud_id = _runtime_value("cloud")
    previous_page = _page(
        cloud_id=cloud_id,
        space_id="space-a",
        page_id="shared-page",
    )
    current_page = _page(
        cloud_id=cloud_id,
        space_id="space-b",
        page_id="shared-page",
    )

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[previous_page],
            suffix="previous",
        ),
        current=_snapshot(cloud_id=cloud_id, pages=[current_page], suffix="current"),
    )

    assert diff_result.summary.new_pages == 1
    assert diff_result.summary.deleted_candidates == 1
    assert diff_result.changed_pages[0].current.page_key == current_page.page_key


def test_same_space_and_page_id_in_different_cloud_is_different_page() -> None:
    previous_cloud_id = _runtime_value("cloud")
    current_cloud_id = _runtime_value("cloud")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=previous_cloud_id,
            pages=[
                _page(
                    cloud_id=previous_cloud_id,
                    space_id="space-a",
                    page_id="shared-page",
                )
            ],
            suffix="previous",
        ),
        current=_snapshot(
            cloud_id=current_cloud_id,
            pages=[
                _page(
                    cloud_id=current_cloud_id,
                    space_id="space-a",
                    page_id="shared-page",
                )
            ],
            suffix="current",
        ),
    )

    assert diff_result.summary.new_pages == 1
    assert diff_result.summary.deleted_candidates == 1


def test_duplicate_page_key_raises_clear_error() -> None:
    cloud_id = _runtime_value("cloud")
    duplicate_a = _page(cloud_id=cloud_id, page_id="page-1")
    duplicate_b = _page(cloud_id=cloud_id, page_id="page-1")

    with pytest.raises(DiffEngineError, match="duplicate page_key"):
        index_snapshot_pages([duplicate_a, duplicate_b], snapshot_label="current")


def test_changed_pages_only_include_new_and_updated() -> None:
    cloud_id = _runtime_value("cloud")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(cloud_id=cloud_id, page_id="updated", version_number=1),
                _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            ],
            suffix="previous",
        ),
        current=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(cloud_id=cloud_id, page_id="new", version_number=1),
                _page(cloud_id=cloud_id, page_id="updated", version_number=2),
                _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            ],
            suffix="current",
        ),
    )

    assert [change.change_type for change in diff_result.changed_pages] == [
        ChangeType.NEW,
        ChangeType.UPDATED,
    ]
    assert {change.current.page_id for change in diff_result.changed_pages} == {
        "new",
        "updated",
    }


def test_deleted_candidates_return_separate_list_and_summary_counts() -> None:
    cloud_id = _runtime_value("cloud")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(cloud_id=cloud_id, page_id="deleted"),
                _page(cloud_id=cloud_id, page_id="updated", version_number=1),
                _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            ],
            suffix="previous",
        ),
        current=_snapshot(
            cloud_id=cloud_id,
            pages=[
                _page(cloud_id=cloud_id, page_id="new"),
                _page(cloud_id=cloud_id, page_id="updated", version_number=2),
                _page(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            ],
            suffix="current",
        ),
    )

    assert [change.previous.page_id for change in diff_result.deleted_candidates] == [
        "deleted"
    ]
    assert diff_result.summary.new_pages == 1
    assert diff_result.summary.updated_pages == 1
    assert diff_result.summary.unchanged_pages == 1
    assert diff_result.summary.deleted_candidates == 1
    assert diff_result.summary.changed_pages == 2


def test_unavailable_previous_page_is_not_misclassified_as_deleted_candidate() -> None:
    cloud_id = _runtime_value("cloud")
    unavailable_page = _page(cloud_id=cloud_id, page_id="unavailable")

    diff_result = diff_snapshots(
        previous=_snapshot(
            cloud_id=cloud_id,
            pages=[unavailable_page],
            suffix="previous",
        ),
        current=_snapshot(cloud_id=cloud_id, pages=[], suffix="current"),
        unavailable_page_keys={str(unavailable_page.page_key)},
    )

    assert diff_result.deleted_candidates == []
    assert [change.change_type for change in diff_result.failed_pages] == [
        ChangeType.FAILED
    ]
    assert diff_result.summary.failed_pages == 1


def test_output_order_is_deterministic_by_page_key() -> None:
    cloud_id = _runtime_value("cloud")
    current_pages = [
        _page(cloud_id=cloud_id, space_id="space-b", page_id="page-2"),
        _page(cloud_id=cloud_id, space_id="space-a", page_id="page-2"),
        _page(cloud_id=cloud_id, space_id="space-a", page_id="page-1"),
    ]

    diff_result = diff_snapshots(
        previous=_snapshot(cloud_id=cloud_id, pages=[], suffix="previous"),
        current=_snapshot(cloud_id=cloud_id, pages=current_pages, suffix="current"),
    )

    assert [change.current.page_key for change in diff_result.changed_pages] == sorted(
        page.page_key for page in current_pages
    )
    assert all(isinstance(change, PageChange) for change in diff_result.changed_pages)
