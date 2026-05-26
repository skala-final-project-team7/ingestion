from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from data_sync_agent.messaging import (
    LocalMessagePayloadWriter,
    build_changed_message_payload,
    build_deleted_item_from_change,
    build_deleted_message_payload,
    build_message_payloads,
)
from data_sync_agent.schemas import (
    ChangeType,
    ChangedDocument,
    DeleteType,
    MessageEventType,
    PageSnapshotItem,
)
from data_sync_agent.sync.diff_engine import PageChange


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _page(
    *,
    cloud_id: str,
    page_id: str = "page-1",
    version_number: int = 3,
) -> PageSnapshotItem:
    return PageSnapshotItem(
        cloud_id=cloud_id,
        space_id="space-1",
        space_key="ENG",
        space_name="Engineering",
        page_id=page_id,
        title=f"Synthetic {page_id}",
        status="current",
        page_url=f"https://example.invalid/wiki/spaces/ENG/pages/{page_id}",
        last_modified_at="2026-05-15T00:00:00Z",
        version_number=version_number,
    )


def _changed_document(
    *,
    sync_id: str,
    cloud_id: str,
    page_id: str = "page-1",
    change_type: ChangeType = ChangeType.UPDATED,
) -> ChangedDocument:
    return ChangedDocument(
        sync_id=sync_id,
        change_type=change_type,
        cloud_id=cloud_id,
        space={
            "space_id": "space-1",
            "space_key": "ENG",
            "space_name": "Engineering",
        },
        page={
            "page_key": f"{cloud_id}:space-1:{page_id}",
            "space_id": "space-1",
            "page_id": page_id,
            "title": f"Synthetic {page_id}",
            "status": "current",
            "page_url": f"https://example.invalid/wiki/pages/{page_id}",
            "last_modified_at": "2026-05-15T00:00:00Z",
            "version_number": 3,
        },
        body={
            "representation": "storage",
            "storage_html": "<p>Synthetic body</p>",
            "plain_text": "Synthetic body",
        },
        metadata={"detected_at": "2026-05-15T02:00:00Z"},
    )


def test_deleted_candidate_diff_item_converts_to_deleted_item() -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    page = _page(cloud_id=cloud_id)
    deleted_item = build_deleted_item_from_change(
        PageChange(
            change_type=ChangeType.DELETED_CANDIDATE,
            page_key=str(page.page_key),
            previous=page,
            current=None,
        ),
        sync_id=sync_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    serialized = deleted_item.to_dict()

    assert serialized["delete_type"] == DeleteType.DELETED_CANDIDATE
    assert serialized["deletion_status"] == "candidate"
    assert serialized["requires_confirmation"] is True
    assert serialized["page_id"] == "page-1"
    assert serialized["space_id"] == "space-1"
    assert serialized["cloud_id"] == cloud_id
    assert serialized["page_url"].endswith("/page-1")
    assert serialized["last_seen_version"] == 3
    assert serialized["detected_at"] == "2026-05-15T02:00:00Z"


def test_changed_document_converts_to_changed_message_payload() -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    document = _changed_document(sync_id=sync_id, cloud_id=cloud_id)

    payload = build_changed_message_payload(
        document,
        payload_ref="changed/changed_documents.jsonl#1",
    )
    serialized = payload.to_dict()

    assert serialized["event_type"] == MessageEventType.CHUNKING_REQUESTED
    assert serialized["operation"] == "page_changed"
    assert serialized["downstream_target"] == "chunking_embedding_pipeline"
    assert serialized["document_id"] == document.document_id
    assert serialized["payload_ref"] == "changed/changed_documents.jsonl#1"
    assert serialized["payload_id"] == (
        f"{sync_id}:chunking_requested:space-1:page-1:{document.document_id}"
    )
    assert serialized["idempotency_key"] == serialized["payload_id"]


def test_deleted_item_converts_to_deleted_candidate_message_payload() -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    deleted_item = build_deleted_item_from_change(
        PageChange(
            change_type=ChangeType.DELETED_CANDIDATE,
            page_key=f"{cloud_id}:space-1:page-1",
            previous=_page(cloud_id=cloud_id),
            current=None,
        ),
        sync_id=sync_id,
        detected_at="2026-05-15T02:00:00Z",
    )

    payload = build_deleted_message_payload(
        deleted_item,
        payload_ref="deleted/deleted_items.jsonl#1",
    )
    serialized = payload.to_dict()

    assert serialized["event_type"] == MessageEventType.DELETE_CANDIDATE_DETECTED
    assert serialized["operation"] == "page_deleted_candidate"
    assert serialized["downstream_target"] == "vector_db_update"
    assert serialized["document_id"] is None
    assert serialized["change_type"] == "deleted_candidate"
    assert serialized["payload_id"] == (
        f"{sync_id}:delete_candidate_detected:space-1:page-1:deleted_candidate"
    )


def test_build_message_payloads_filters_unchanged_failed_and_skipped() -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    changed_document = _changed_document(sync_id=sync_id, cloud_id=cloud_id)
    page = _page(cloud_id=cloud_id, page_id="deleted")
    deleted_item = build_deleted_item_from_change(
        PageChange(
            change_type=ChangeType.DELETED_CANDIDATE,
            page_key=str(page.page_key),
            previous=page,
            current=None,
        ),
        sync_id=sync_id,
        detected_at="2026-05-15T02:00:00Z",
    )
    unchanged_change = PageChange(
        change_type=ChangeType.UNCHANGED,
        page_key=f"{cloud_id}:space-1:unchanged",
        previous=_page(cloud_id=cloud_id, page_id="unchanged"),
        current=_page(cloud_id=cloud_id, page_id="unchanged"),
    )
    failed_change = PageChange(
        change_type=ChangeType.FAILED,
        page_key=f"{cloud_id}:space-1:failed",
        previous=_page(cloud_id=cloud_id, page_id="failed"),
        current=None,
    )

    payloads = build_message_payloads(
        changed_documents=[changed_document],
        deleted_items=[deleted_item],
        skipped_changes=[unchanged_change, failed_change],
    )

    assert [payload.event_type for payload in payloads] == [
        MessageEventType.CHUNKING_REQUESTED,
        MessageEventType.DELETE_CANDIDATE_DETECTED,
    ]


def test_message_payload_schema_requires_core_fields() -> None:
    document = _changed_document(
        sync_id=_runtime_value("sync"),
        cloud_id=_runtime_value("cloud"),
    )
    payload = build_changed_message_payload(
        document,
        payload_ref="changed/changed_documents.jsonl#1",
    )

    serialized = payload.to_dict()

    assert serialized["payload_id"]
    assert serialized["idempotency_key"]
    assert serialized["sync_id"]
    assert serialized["source_type"] == "confluence_page"
    assert serialized["page_id"] == "page-1"
    assert serialized["space_id"] == "space-1"
    assert serialized["payload_ref"]


def test_local_message_writer_writes_jsonl(tmp_path: Path) -> None:
    document = _changed_document(
        sync_id=_runtime_value("sync"),
        cloud_id=_runtime_value("cloud"),
    )
    payload = build_changed_message_payload(
        document,
        payload_ref="changed/changed_documents.jsonl#1",
    )
    writer = LocalMessagePayloadWriter(output_dir=tmp_path)

    output_path = writer.write([payload])

    assert output_path == tmp_path / "messages" / "message_payloads.jsonl"
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["payload_id"] == payload.payload_id


def test_payloads_and_deleted_items_do_not_include_sensitive_values() -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    sensitive_value = _runtime_value("runtime-token")
    secret_like_value = _runtime_value("secret-like")
    document = _changed_document(sync_id=sync_id, cloud_id=cloud_id)
    page = _page(cloud_id=cloud_id, page_id="deleted")
    deleted_item = build_deleted_item_from_change(
        PageChange(
            change_type=ChangeType.DELETED_CANDIDATE,
            page_key=str(page.page_key),
            previous=page,
            current=None,
        ),
        sync_id=sync_id,
        detected_at="2026-05-15T02:00:00Z",
    )
    payloads = build_message_payloads(
        changed_documents=[document],
        deleted_items=[deleted_item],
        skipped_changes=[],
    )

    serialized = str(deleted_item.to_dict()) + str(
        [payload.to_dict() for payload in payloads]
    )

    assert sensitive_value not in serialized
    assert secret_like_value not in serialized
    assert "access_token" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized
