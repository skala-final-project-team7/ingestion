from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from data_sync_agent.config import DataSyncConfig
from data_sync_agent.schemas import (
    ChangeType,
    ChangedDocument,
    DeletedItem,
    DeleteType,
    FailedItem,
    FailedItemStage,
    FailedItemType,
    MessageEventType,
    MessagePayload,
    PageSnapshot,
    PageSnapshotItem,
    SyncJob,
    SyncJobStatus,
    SyncReport,
    SyncReportCounts,
    SyncReportStatus,
)


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _snapshot_item(
    *,
    cloud_id: str | None = None,
    page_id: str = "page-123",
    version_number: int = 7,
) -> PageSnapshotItem:
    resolved_cloud_id = cloud_id or _runtime_value("cloud")
    return PageSnapshotItem(
        cloud_id=resolved_cloud_id,
        space_id="space-1",
        space_key="ENG",
        space_name="Engineering",
        page_id=page_id,
        title="Synthetic Page",
        status="current",
        page_url="https://example.invalid/wiki/spaces/ENG/pages/page-123",
        last_modified_at="2026-05-14T00:10:00Z",
        version_number=version_number,
    )


def test_config_accepts_external_inputs_and_redacts_token(tmp_path: Path) -> None:
    access_token = _runtime_value("runtime-token")
    cloud_id = _runtime_value("cloud")
    previous_snapshot = tmp_path / "snapshots" / "previous.json"

    config = DataSyncConfig(
        cloud_id=cloud_id,
        access_token=access_token,
        output_dir=tmp_path,
        previous_snapshot=previous_snapshot,
    )

    assert config.cloud_id == cloud_id
    assert config.output_dir == tmp_path
    assert config.previous_snapshot == previous_snapshot
    assert config.request_delay_seconds == 0.3
    assert config.max_retries == 3
    assert config.timeout_seconds == 20

    safe_dict = config.to_safe_dict()

    assert safe_dict["access_token"] == "<redacted>"
    assert access_token not in repr(config)
    assert access_token not in str(safe_dict)


@pytest.mark.parametrize(
    ("field_name", "kwargs", "match"),
    [
        ("cloud_id", {"cloud_id": ""}, "cloud_id"),
        ("access_token", {"access_token": ""}, "access_token"),
        ("output_dir", {"output_dir": ""}, "output_dir"),
        ("previous_snapshot", {"previous_snapshot": ""}, "previous_snapshot"),
    ],
)
def test_config_rejects_missing_required_values(
    tmp_path: Path,
    field_name: str,
    kwargs: dict[str, str],
    match: str,
) -> None:
    defaults = {
        "cloud_id": _runtime_value("cloud"),
        "access_token": _runtime_value("runtime-token"),
        "output_dir": tmp_path,
        "previous_snapshot": tmp_path / "previous.json",
    }
    defaults.update(kwargs)

    with pytest.raises(ValueError, match=match):
        DataSyncConfig(**defaults)

    assert field_name in defaults


def test_page_snapshot_schema_preserves_required_page_metadata() -> None:
    cloud_id = _runtime_value("cloud")
    page = _snapshot_item(cloud_id=cloud_id)
    snapshot = PageSnapshot(
        snapshot_id=_runtime_value("snapshot"),
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        created_at="2026-05-14T00:20:00Z",
        pages=[page],
    )

    assert page.page_key == f"{cloud_id}:space-1:page-123"
    assert page.version_number == 7
    assert page.last_modified_at == "2026-05-14T00:10:00Z"

    serialized = snapshot.to_dict()

    assert serialized["pages"][0]["page_key"] == f"{cloud_id}:space-1:page-123"
    assert serialized["pages"][0]["space_id"] == "space-1"
    assert serialized["pages"][0]["version_number"] == 7


def test_sync_job_schema_preserves_job_identity_without_token() -> None:
    access_token = _runtime_value("runtime-token")
    sync_job = SyncJob(
        sync_id=_runtime_value("sync"),
        cloud_id=_runtime_value("cloud"),
        status=SyncJobStatus.PENDING,
        requested_at="2026-05-14T00:00:00Z",
        output_dir="data",
        previous_snapshot="data/snapshots/latest_snapshot.json",
    )

    serialized = sync_job.to_dict()

    assert serialized["status"] == "pending"
    assert serialized["output_dir"] == "data"
    assert "access_token" not in serialized
    assert access_token not in str(serialized)


def test_changed_document_schema_preserves_sync_fields_and_body() -> None:
    changed_document = ChangedDocument(
        sync_id=_runtime_value("sync"),
        change_type=ChangeType.NEW,
        cloud_id=_runtime_value("cloud"),
        space={
            "space_id": "space-1",
            "space_key": "ENG",
            "space_name": "Engineering",
        },
        page={
            "page_id": "page-123",
            "title": "Synthetic Page",
            "status": "current",
            "page_url": "https://example.invalid/wiki/spaces/ENG/pages/page-123",
            "last_modified_at": "2026-05-14T00:10:00Z",
            "version_number": 7,
        },
        body={
            "representation": "storage",
            "storage_html": "<h1>Synthetic Page</h1>",
            "plain_text": "Synthetic Page",
        },
    )

    serialized = changed_document.to_dict()

    assert changed_document.document_id == "confluence-page-page-123-7"
    assert serialized["source_type"] == "confluence_page"
    assert serialized["sync_id"] == changed_document.sync_id
    assert serialized["change_type"] == "new"
    assert serialized["body"]["storage_html"] == "<h1>Synthetic Page</h1>"
    assert serialized["body"]["plain_text"] == "Synthetic Page"
    assert (
        serialized["metadata"]["attachment_processing_status"]
        == "not_supported_in_mvp"
    )


def test_deleted_item_schema_represents_delete_candidate() -> None:
    sync_id = _runtime_value("sync")
    cloud_id = _runtime_value("cloud")
    deleted_item = DeletedItem(
        sync_id=sync_id,
        cloud_id=cloud_id,
        space_id="space-1",
        page_id="page-123",
        title="Synthetic Page",
    )

    serialized = deleted_item.to_dict()

    assert serialized["delete_type"] == DeleteType.DELETED_CANDIDATE
    assert serialized["page_key"] == f"{cloud_id}:space-1:page-123"
    assert serialized["detection_method"] == "snapshot_missing"
    assert serialized["requires_confirmation"] is True


def test_message_payload_schema_supports_downstream_event_contract() -> None:
    chunking_payload = MessagePayload(
        sync_id=_runtime_value("sync"),
        event_type=MessageEventType.CHUNKING_REQUESTED,
        page_id="page-123",
        space_id="space-1",
        document_id="confluence-page-page-123-7",
        change_type=ChangeType.UPDATED,
        payload_ref="changed/changed_documents.jsonl#1",
    )
    delete_payload = MessagePayload(
        sync_id=_runtime_value("sync"),
        event_type=MessageEventType.DELETE_CANDIDATE_DETECTED,
        page_id="page-456",
        space_id="space-1",
        document_id=None,
        change_type=ChangeType.DELETED_CANDIDATE,
        payload_ref="deleted/deleted_items.jsonl#1",
    )

    assert chunking_payload.to_dict()["event_type"] == "chunking_requested"
    assert chunking_payload.to_dict()["source_type"] == "confluence_page"
    assert delete_payload.to_dict()["event_type"] == "delete_candidate_detected"
    assert delete_payload.to_dict()["document_id"] is None


def test_sync_report_and_failed_item_schemas_validate_counts() -> None:
    report = SyncReport(
        sync_id=_runtime_value("sync"),
        status=SyncReportStatus.COMPLETED_WITH_ERRORS,
        counts=SyncReportCounts(
            spaces=2,
            pages_seen=5,
            new_pages=1,
            updated_pages=1,
            unchanged_pages=2,
            deleted_candidates=1,
            failed_items=1,
        ),
        output_paths={
            "changed": "changed/changed_documents.jsonl",
            "report": "reports/sync_report.json",
        },
    )
    failed_item = FailedItem(
        sync_id=report.sync_id,
        stage=FailedItemStage.FETCH_PAGE_METADATA,
        item_type=FailedItemType.PAGE,
        item_id="page-123",
        error_type="PermissionDenied",
        error_message="Page metadata request was denied.",
        retryable=False,
        attempt_count=1,
    )

    assert report.to_dict()["counts"]["deleted_candidates"] == 1
    assert failed_item.to_dict()["stage"] == "fetch_page_metadata"
    assert failed_item.to_dict()["status"] == "failed"

    with pytest.raises(ValueError, match="failed_items"):
        SyncReportCounts(failed_items=-1)
