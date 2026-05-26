from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from data_sync_agent.schemas import PageSnapshot, PageSnapshotItem
from data_sync_agent.sync.snapshot_repository import (
    LOCAL_SNAPSHOT_FORMAT_VERSION,
    LocalSnapshotRepository,
    SnapshotRepositoryError,
)


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _snapshot_item(cloud_id: str, page_id: str = "page-123") -> PageSnapshotItem:
    return PageSnapshotItem(
        cloud_id=cloud_id,
        space_id="space-1",
        space_key="ENG",
        space_name="Engineering",
        page_id=page_id,
        title="Synthetic Page",
        status="current",
        page_url="https://example.invalid/wiki/spaces/ENG/pages/page-123",
        last_modified_at="2026-05-15T00:10:00Z",
        version_number=7,
    )


def _snapshot(cloud_id: str) -> PageSnapshot:
    return PageSnapshot(
        snapshot_id=_runtime_value("snapshot"),
        sync_id=_runtime_value("sync"),
        cloud_id=cloud_id,
        created_at="2026-05-15T00:20:00Z",
        pages=[_snapshot_item(cloud_id)],
    )


def test_load_previous_snapshot_restores_page_snapshot_schema(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    snapshot = _snapshot(cloud_id)
    snapshot_path = tmp_path / "snapshots" / "previous.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "format_version": LOCAL_SNAPSHOT_FORMAT_VERSION,
                "generated_at": "2026-05-15T00:30:00Z",
                "snapshot": snapshot.to_dict(),
            },
        ),
        encoding="utf-8",
    )

    repository = LocalSnapshotRepository(output_dir=tmp_path)
    restored = repository.load_previous_snapshot(
        snapshot_path,
        cloud_id=cloud_id,
        sync_id=_runtime_value("sync"),
    )

    assert restored.snapshot_id == snapshot.snapshot_id
    assert restored.cloud_id == cloud_id
    assert restored.pages[0].page_key == f"{cloud_id}:space-1:page-123"
    assert restored.pages[0].version_number == 7


def test_load_previous_snapshot_returns_empty_snapshot_when_file_missing(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    sync_id = _runtime_value("sync")
    missing_path = tmp_path / "snapshots" / "missing.json"

    repository = LocalSnapshotRepository(output_dir=tmp_path)
    snapshot = repository.load_previous_snapshot(
        missing_path,
        cloud_id=cloud_id,
        sync_id=sync_id,
        generated_at="2026-05-15T01:00:00Z",
    )

    assert snapshot.snapshot_id == f"empty-previous-{sync_id}"
    assert snapshot.sync_id == sync_id
    assert snapshot.cloud_id == cloud_id
    assert snapshot.created_at == "2026-05-15T01:00:00Z"
    assert snapshot.pages == []


def test_load_previous_snapshot_raises_clear_error_for_malformed_json(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "snapshots" / "malformed.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text("{not-json", encoding="utf-8")

    repository = LocalSnapshotRepository(output_dir=tmp_path)

    with pytest.raises(SnapshotRepositoryError, match="Malformed snapshot JSON"):
        repository.load_previous_snapshot(
            snapshot_path,
            cloud_id=_runtime_value("cloud"),
            sync_id=_runtime_value("sync"),
        )


def test_load_previous_snapshot_raises_clear_error_for_invalid_schema(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "snapshots" / "invalid.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "format_version": LOCAL_SNAPSHOT_FORMAT_VERSION,
                "generated_at": "2026-05-15T01:00:00Z",
                "snapshot": {
                    "snapshot_id": _runtime_value("snapshot"),
                    "sync_id": _runtime_value("sync"),
                    "cloud_id": _runtime_value("cloud"),
                    "created_at": "2026-05-15T00:20:00Z",
                    "pages": [{"page_id": "missing-required-fields"}],
                },
            },
        ),
        encoding="utf-8",
    )

    repository = LocalSnapshotRepository(output_dir=tmp_path)

    with pytest.raises(SnapshotRepositoryError, match="Invalid snapshot schema"):
        repository.load_previous_snapshot(
            snapshot_path,
            cloud_id=_runtime_value("cloud"),
            sync_id=_runtime_value("sync"),
        )


def test_save_current_snapshot_writes_json_and_creates_directory(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    snapshot = _snapshot(cloud_id)
    snapshot_path = tmp_path / "nested" / "snapshots" / "current.json"

    repository = LocalSnapshotRepository(output_dir=tmp_path)
    result = repository.save_current_snapshot(
        snapshot,
        snapshot_path=snapshot_path,
        generated_at="2026-05-15T02:00:00Z",
    )

    assert result.path == snapshot_path
    assert result.format_version == LOCAL_SNAPSHOT_FORMAT_VERSION
    assert snapshot_path.exists()

    written = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert written["format_version"] == LOCAL_SNAPSHOT_FORMAT_VERSION
    assert written["generated_at"] == "2026-05-15T02:00:00Z"
    assert written["snapshot"]["pages"][0]["page_key"] == f"{cloud_id}:space-1:page-123"


def test_latest_snapshot_path_uses_output_dir_snapshots_directory(
    tmp_path: Path,
) -> None:
    repository = LocalSnapshotRepository(output_dir=tmp_path)

    assert repository.latest_snapshot_path() == (
        tmp_path / "snapshots" / "latest_snapshot.json"
    )


def test_saved_snapshot_excludes_sensitive_runtime_values(tmp_path: Path) -> None:
    cloud_id = _runtime_value("cloud")
    snapshot = _snapshot(cloud_id)
    sensitive_marker = _runtime_value("sensitive-marker")

    repository = LocalSnapshotRepository(output_dir=tmp_path)
    result = repository.save_current_snapshot(snapshot)

    written_text = result.path.read_text(encoding="utf-8")

    assert "access_token" not in written_text
    assert "Authorization" not in written_text
    assert "Bearer" not in written_text
    assert sensitive_marker not in written_text


def test_repository_round_trip_is_compatible_with_page_snapshot_schema(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    current_snapshot = _snapshot(cloud_id)
    repository = LocalSnapshotRepository(output_dir=tmp_path)

    result = repository.save_current_snapshot(current_snapshot)
    restored_snapshot = repository.load_previous_snapshot(
        result.path,
        cloud_id=cloud_id,
        sync_id=_runtime_value("sync"),
    )

    assert isinstance(restored_snapshot, PageSnapshot)
    assert isinstance(restored_snapshot.pages[0], PageSnapshotItem)
    assert restored_snapshot.to_dict() == current_snapshot.to_dict()
