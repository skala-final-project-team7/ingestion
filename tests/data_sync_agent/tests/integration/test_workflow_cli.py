from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from data_sync_agent.config import DataSyncConfig
from data_sync_agent.scripts import run_delta_sync
from data_sync_agent.schemas import PageSnapshot, PageSnapshotItem, SyncReportStatus
from data_sync_agent.sync.snapshot_repository import (
    LOCAL_SNAPSHOT_FORMAT_VERSION,
    LocalSnapshotRepository,
)
from data_sync_agent.workflow import (
    build_data_sync_workflow,
    run_data_sync_workflow,
)


class FakeConfluenceClient:
    def __init__(
        self,
        *,
        spaces: list[dict],
        pages_by_space: dict[str, list[dict]],
        details_by_page: dict[str, dict | Exception],
    ) -> None:
        self.spaces = spaces
        self.pages_by_space = pages_by_space
        self.details_by_page = details_by_page
        self.requested_page_ids: list[str] = []

    def list_spaces(self) -> list[dict]:
        return self.spaces

    def list_space_pages(self, space_id: str) -> list[dict]:
        return self.pages_by_space.get(space_id, [])

    def get_page_detail(self, page_id: str) -> dict:
        self.requested_page_ids.append(page_id)
        detail = self.details_by_page[page_id]
        if isinstance(detail, Exception):
            raise detail
        return detail


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _config(
    *,
    tmp_path: Path,
    cloud_id: str,
    previous_snapshot: Path,
    access_token: str | None = None,
) -> DataSyncConfig:
    return DataSyncConfig(
        cloud_id=cloud_id,
        access_token=access_token or _runtime_value("synthetic-token"),
        previous_snapshot=previous_snapshot,
        output_dir=tmp_path / "output",
        request_delay_seconds=0,
        max_retries=0,
        timeout_seconds=5,
    )


def _snapshot_item(
    *,
    cloud_id: str,
    page_id: str,
    version_number: int,
    last_modified_at: str = "2026-05-15T00:00:00Z",
) -> PageSnapshotItem:
    return PageSnapshotItem(
        cloud_id=cloud_id,
        space_id="space-1",
        space_key="ENG",
        space_name="Engineering",
        page_id=page_id,
        title=f"Synthetic {page_id}",
        status="current",
        page_url=f"https://example.invalid/wiki/pages/{page_id}",
        last_modified_at=last_modified_at,
        version_number=version_number,
    )


def _previous_snapshot_path(
    *,
    tmp_path: Path,
    cloud_id: str,
    pages: list[PageSnapshotItem],
) -> Path:
    snapshot = PageSnapshot(
        snapshot_id=_runtime_value("snapshot"),
        sync_id=_runtime_value("previous-sync"),
        cloud_id=cloud_id,
        created_at="2026-05-15T00:00:00Z",
        pages=pages,
    )
    path = tmp_path / "previous" / "latest_snapshot.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "format_version": LOCAL_SNAPSHOT_FORMAT_VERSION,
                "generated_at": "2026-05-15T00:00:00Z",
                "snapshot": snapshot.to_dict(),
            },
        ),
        encoding="utf-8",
    )
    return path


def _space() -> dict:
    return {"id": "space-1", "key": "ENG", "name": "Engineering"}


def _page_metadata(
    *,
    page_id: str,
    version_number: int,
    created_at: str | None = None,
) -> dict:
    return {
        "id": page_id,
        "title": f"Synthetic {page_id}",
        "status": "current",
        "_links": {"webui": f"https://example.invalid/wiki/pages/{page_id}"},
        "version": {
            "number": version_number,
            "createdAt": created_at or f"2026-05-15T0{version_number}:00:00Z",
        },
    }


def _page_detail(*, page_id: str, version_number: int, html: str = "<p>ok</p>") -> dict:
    return {
        "id": page_id,
        "title": f"Synthetic {page_id}",
        "status": "current",
        "_links": {"webui": f"https://example.invalid/wiki/pages/{page_id}"},
        "version": {
            "number": version_number,
            "createdAt": f"2026-05-15T0{version_number}:30:00Z",
        },
        "body": {"storage": {"value": html, "representation": "storage"}},
    }


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_workflow_generates_changed_deleted_messages_report_and_snapshot(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    previous_snapshot = _previous_snapshot_path(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        pages=[
            _snapshot_item(cloud_id=cloud_id, page_id="updated", version_number=1),
            _snapshot_item(cloud_id=cloud_id, page_id="unchanged", version_number=1),
            _snapshot_item(cloud_id=cloud_id, page_id="deleted", version_number=1),
        ],
    )
    config = _config(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        previous_snapshot=previous_snapshot,
    )
    client = FakeConfluenceClient(
        spaces=[_space()],
        pages_by_space={
            "space-1": [
                _page_metadata(page_id="new", version_number=1),
                _page_metadata(page_id="updated", version_number=2),
                _page_metadata(
                    page_id="unchanged",
                    version_number=1,
                    created_at="2026-05-15T00:00:00Z",
                ),
            ]
        },
        details_by_page={
            "new": _page_detail(page_id="new", version_number=1, html="<h1>New</h1>"),
            "updated": _page_detail(
                page_id="updated",
                version_number=2,
                html="<p>Updated</p>",
            ),
        },
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T03:00:00Z",
    )

    assert result.report.status == SyncReportStatus.COMPLETED
    assert client.requested_page_ids == ["new", "updated"]
    assert [document.change_type for document in result.changed_documents] == [
        "new",
        "updated",
    ]
    assert [item.page_id for item in result.deleted_items] == ["deleted"]
    assert [payload.event_type for payload in result.message_payloads] == [
        "chunking_requested",
        "chunking_requested",
        "delete_candidate_detected",
    ]
    assert result.output_paths["current_snapshot"].endswith(
        "snapshots/latest_snapshot.json"
    )

    changed_documents = _read_jsonl(config.output_dir / "changed" / "changed_documents.jsonl")
    deleted_items = _read_jsonl(config.output_dir / "deleted" / "deleted_items.jsonl")
    message_payloads = _read_jsonl(config.output_dir / "messages" / "message_payloads.jsonl")
    report = json.loads(
        (config.output_dir / "reports" / "sync_report.json").read_text(
            encoding="utf-8",
        )
    )
    saved_snapshot = LocalSnapshotRepository(config.output_dir).load_previous_snapshot(
        config.output_dir / "snapshots" / "latest_snapshot.json",
        cloud_id=cloud_id,
        sync_id=_runtime_value("sync"),
    )

    assert len(changed_documents) == 2
    assert changed_documents[0]["body"]["storage_html"] == "<h1>New</h1>"
    assert changed_documents[0]["body"]["plain_text"] == "New"
    assert deleted_items[0]["deletion_status"] == "candidate"
    assert len(message_payloads) == 3
    assert report["counts"]["new_pages"] == 1
    assert report["counts"]["updated_pages"] == 1
    assert report["counts"]["unchanged_pages"] == 1
    assert report["counts"]["deleted_candidates"] == 1
    assert len(saved_snapshot.pages) == 3


def test_workflow_treats_missing_previous_snapshot_as_all_new(tmp_path: Path) -> None:
    cloud_id = _runtime_value("cloud")
    config = _config(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        previous_snapshot=tmp_path / "missing" / "snapshot.json",
    )
    client = FakeConfluenceClient(
        spaces=[_space()],
        pages_by_space={"space-1": [_page_metadata(page_id="new", version_number=1)]},
        details_by_page={"new": _page_detail(page_id="new", version_number=1)},
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T03:00:00Z",
    )

    assert result.report.counts.new_pages == 1
    assert result.report.counts.deleted_candidates == 0
    assert client.requested_page_ids == ["new"]


def test_workflow_treats_empty_current_snapshot_as_deleted_candidates(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    previous_snapshot = _previous_snapshot_path(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        pages=[_snapshot_item(cloud_id=cloud_id, page_id="old", version_number=1)],
    )
    config = _config(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        previous_snapshot=previous_snapshot,
    )
    client = FakeConfluenceClient(
        spaces=[_space()],
        pages_by_space={"space-1": []},
        details_by_page={},
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T03:00:00Z",
    )

    assert result.report.counts.pages_seen == 0
    assert result.report.counts.deleted_candidates == 1
    assert client.requested_page_ids == []
    assert result.deleted_items[0].page_id == "old"


def test_workflow_records_partial_detail_failure_without_stopping(
    tmp_path: Path,
) -> None:
    cloud_id = _runtime_value("cloud")
    config = _config(
        tmp_path=tmp_path,
        cloud_id=cloud_id,
        previous_snapshot=tmp_path / "missing" / "snapshot.json",
    )
    client = FakeConfluenceClient(
        spaces=[_space()],
        pages_by_space={
            "space-1": [
                _page_metadata(page_id="ok", version_number=1),
                _page_metadata(page_id="failed", version_number=1),
            ]
        },
        details_by_page={
            "ok": _page_detail(page_id="ok", version_number=1),
            "failed": RuntimeError("detail failed Authorization Bearer access_token"),
        },
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T03:00:00Z",
    )

    assert result.report.status == SyncReportStatus.COMPLETED_WITH_ERRORS
    assert [document.page["page_id"] for document in result.changed_documents] == ["ok"]
    assert result.failed_items[0].item_id == "failed"
    failed_text = (config.output_dir / "failed" / "failed_items.jsonl").read_text(
        encoding="utf-8",
    )
    assert "Authorization" not in failed_text
    assert "Bearer" not in failed_text
    assert "access_token" not in failed_text


def test_cli_builds_config_runs_workflow_and_redacts_token(
    tmp_path: Path,
    capsys,
) -> None:
    cloud_id = _runtime_value("cloud")
    access_token = _runtime_value("runtime-token")
    previous_snapshot = tmp_path / "missing" / "snapshot.json"
    client = FakeConfluenceClient(
        spaces=[_space()],
        pages_by_space={"space-1": [_page_metadata(page_id="new", version_number=1)]},
        details_by_page={"new": _page_detail(page_id="new", version_number=1)},
    )

    exit_code = run_delta_sync.main(
        [
            "--cloud-id",
            cloud_id,
            "--access-token",
            access_token,
            "--previous-snapshot",
            str(previous_snapshot),
            "--output-dir",
            str(tmp_path / "output"),
            "--request-delay",
            "0",
            "--max-retries",
            "0",
            "--timeout",
            "5",
        ],
        client=client,
        now=lambda: "2026-05-15T03:00:00Z",
    )

    captured = capsys.readouterr()
    combined_output = captured.out + captured.err

    assert exit_code == 0
    assert "status=completed" in combined_output
    assert access_token not in combined_output
    assert "Authorization" not in combined_output
    assert (tmp_path / "output" / "reports" / "sync_report.json").exists()


def test_workflow_builder_falls_back_when_langgraph_is_not_installed() -> None:
    workflow = build_data_sync_workflow(force_sequential=True)

    assert workflow.uses_langgraph is False
    assert workflow.mode == "sequential_fallback"
