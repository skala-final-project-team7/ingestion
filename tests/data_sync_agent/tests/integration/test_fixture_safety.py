from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile
from uuid import uuid4

from data_sync_agent.config import DataSyncConfig
from data_sync_agent.scripts import run_delta_sync
from data_sync_agent.schemas import SyncReportStatus
from data_sync_agent.workflow import run_data_sync_workflow

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sync"
SYNTHETIC_CLOUD_ID = "synthetic-cloud-fixture"
SENSITIVE_WORDS = (
    "access_token",
    "Authorization",
    "Bearer",
    "secret-like",
    "runtime-token",
)


class FixtureConfluenceClient:
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


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _copy_previous_snapshot(tmp_path: Path, name: str = "previous_snapshot.json") -> Path:
    target_path = tmp_path / "snapshots" / name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    copyfile(FIXTURE_DIR / name, target_path)
    return target_path


def _config(
    *,
    tmp_path: Path,
    previous_snapshot: Path,
    access_token: str | None = None,
) -> DataSyncConfig:
    return DataSyncConfig(
        cloud_id=SYNTHETIC_CLOUD_ID,
        access_token=access_token or _runtime_value("synthetic-runtime-value"),
        previous_snapshot=previous_snapshot,
        output_dir=tmp_path / "output",
        request_delay_seconds=0,
        max_retries=0,
        timeout_seconds=5,
    )


def _full_fixture_client() -> FixtureConfluenceClient:
    return FixtureConfluenceClient(
        spaces=_load_fixture("spaces.json")["results"],
        pages_by_space=_load_fixture("current_pages.json"),
        details_by_page=_load_fixture("page_details.json"),
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _all_output_text(output_dir: Path) -> str:
    chunks: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def test_sync_fixtures_are_synthetic_and_exclude_sensitive_values() -> None:
    fixture_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    )

    assert "example.invalid" in fixture_text
    assert "synthetic" in fixture_text
    for sensitive_word in SENSITIVE_WORDS:
        assert sensitive_word not in fixture_text


def test_fixture_full_workflow_outputs_shapes_counts_and_safety(
    tmp_path: Path,
) -> None:
    access_token = _runtime_value("runtime-token")
    previous_snapshot = _copy_previous_snapshot(tmp_path)
    config = _config(
        tmp_path=tmp_path,
        previous_snapshot=previous_snapshot,
        access_token=access_token,
    )

    result = run_data_sync_workflow(
        config=config,
        client=_full_fixture_client(),
        now=lambda: "2026-05-15T04:00:00Z",
    )

    output_dir = config.output_dir
    changed_documents = _read_jsonl(output_dir / "changed" / "changed_documents.jsonl")
    deleted_items = _read_jsonl(output_dir / "deleted" / "deleted_items.jsonl")
    message_payloads = _read_jsonl(output_dir / "messages" / "message_payloads.jsonl")
    failed_items = _read_jsonl(output_dir / "failed" / "failed_items.jsonl")
    report = json.loads(
        (output_dir / "reports" / "sync_report.json").read_text(encoding="utf-8")
    )
    current_snapshot = json.loads(
        (output_dir / "snapshots" / "latest_snapshot.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.report.status == SyncReportStatus.COMPLETED
    assert (output_dir / "snapshots" / "latest_snapshot.json").exists()
    assert (output_dir / "changed" / "changed_documents.jsonl").exists()
    assert (output_dir / "deleted" / "deleted_items.jsonl").exists()
    assert (output_dir / "messages" / "message_payloads.jsonl").exists()
    assert (output_dir / "reports" / "sync_report.json").exists()
    assert (output_dir / "failed" / "failed_items.jsonl").exists()

    assert report["counts"] == {
        "deleted_candidates": 1,
        "failed_items": 0,
        "new_pages": 2,
        "pages_seen": 4,
        "spaces": 1,
        "unchanged_pages": 1,
        "updated_pages": 1,
    }
    assert current_snapshot["snapshot"]["cloud_id"] == SYNTHETIC_CLOUD_ID
    assert len(current_snapshot["snapshot"]["pages"]) == 4
    assert failed_items == []

    assert {document["change_type"] for document in changed_documents} == {
        "new",
        "updated",
    }
    first_document = changed_documents[0]
    for required_key in (
        "document_id",
        "sync_id",
        "source_type",
        "change_type",
        "cloud_id",
        "space",
        "page",
        "body",
        "metadata",
    ):
        assert required_key in first_document
    assert first_document["body"]["storage_html"]
    assert first_document["body"]["plain_text"]

    unsupported_document = next(
        document
        for document in changed_documents
        if document["page"]["page_id"] == "unsupported-page"
    )
    assert unsupported_document["metadata"]["attachment_processing_status"] == (
        "not_supported_in_mvp"
    )
    assert unsupported_document["metadata"]["has_unsupported_content"] is True
    assert "Visible unsupported content text" in unsupported_document["body"]["plain_text"]

    assert len(deleted_items) == 1
    assert deleted_items[0]["delete_type"] == "deleted_candidate"
    assert deleted_items[0]["deletion_status"] == "candidate"
    assert deleted_items[0]["requires_confirmation"] is True

    assert len(message_payloads) == 4
    for payload in message_payloads:
        assert payload["operation"]
        assert payload["downstream_target"]
        assert payload["idempotency_key"]

    output_text = _all_output_text(output_dir)
    assert access_token not in output_text
    for sensitive_word in SENSITIVE_WORDS[:3]:
        assert sensitive_word not in output_text


def test_fixture_missing_previous_snapshot_treats_all_current_pages_as_new(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path=tmp_path,
        previous_snapshot=tmp_path / "missing" / "latest_snapshot.json",
    )

    result = run_data_sync_workflow(
        config=config,
        client=_full_fixture_client(),
        now=lambda: "2026-05-15T04:00:00Z",
    )

    assert result.report.counts.new_pages == 4
    assert result.report.counts.updated_pages == 0
    assert result.report.counts.unchanged_pages == 0
    assert result.report.counts.deleted_candidates == 0


def test_fixture_empty_current_marks_previous_pages_as_deleted_candidates(
    tmp_path: Path,
) -> None:
    previous_snapshot = _copy_previous_snapshot(tmp_path)
    config = _config(tmp_path=tmp_path, previous_snapshot=previous_snapshot)
    client = FixtureConfluenceClient(
        spaces=_load_fixture("spaces.json")["results"],
        pages_by_space=_load_fixture("empty_pages.json"),
        details_by_page={},
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T04:00:00Z",
    )

    assert result.report.status == SyncReportStatus.COMPLETED
    assert result.report.counts.pages_seen == 0
    assert result.report.counts.deleted_candidates == 3
    assert client.requested_page_ids == []


def test_fixture_partial_failure_records_failed_page_and_partial_success(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path=tmp_path,
        previous_snapshot=tmp_path / "missing" / "latest_snapshot.json",
    )
    client = FixtureConfluenceClient(
        spaces=_load_fixture("spaces.json")["results"],
        pages_by_space=_load_fixture("partial_failure_pages.json"),
        details_by_page={
            "ok-page": {
                "_links": {"webui": "https://example.invalid/wiki/pages/ok-page"},
                "body": {
                    "storage": {
                        "representation": "storage",
                        "value": "<p>OK body</p>",
                    }
                },
                "id": "ok-page",
                "status": "current",
                "title": "Synthetic OK Page",
                "version": {"createdAt": "2026-05-15T01:00:00Z", "number": 1},
            },
            "failure-page": RuntimeError(
                "Synthetic detail failure with Authorization Bearer access_token"
            ),
        },
    )

    result = run_data_sync_workflow(
        config=config,
        client=client,
        now=lambda: "2026-05-15T04:00:00Z",
    )

    failed_items = _read_jsonl(config.output_dir / "failed" / "failed_items.jsonl")

    assert result.report.status == SyncReportStatus.COMPLETED_WITH_ERRORS
    assert result.report.counts.failed_items == 1
    assert failed_items[0]["item_id"] == "failure-page"
    assert failed_items[0]["stage"] == "fetch_page_detail"
    failed_text = json.dumps(failed_items)
    assert "Authorization" not in failed_text
    assert "Bearer" not in failed_text
    assert "access_token" not in failed_text


def test_malformed_previous_snapshot_boundary_records_failure(tmp_path: Path) -> None:
    malformed_path = tmp_path / "snapshots" / "malformed.json"
    malformed_path.parent.mkdir(parents=True)
    malformed_path.write_text("{not-valid-json", encoding="utf-8")
    config = _config(tmp_path=tmp_path, previous_snapshot=malformed_path)

    result = run_data_sync_workflow(
        config=config,
        client=_full_fixture_client(),
        now=lambda: "2026-05-15T04:00:00Z",
    )

    assert result.report.status == SyncReportStatus.COMPLETED_WITH_ERRORS
    assert result.failed_items[0].stage == "load_previous_snapshot"
    assert result.failed_items[0].item_type == "snapshot"


def test_cli_fixture_output_redacts_runtime_token_and_auth_words(
    tmp_path: Path,
    capsys,
) -> None:
    access_token = _runtime_value("runtime-token")
    previous_snapshot = _copy_previous_snapshot(tmp_path)
    output_dir = tmp_path / "output"

    exit_code = run_delta_sync.main(
        [
            "--cloud-id",
            SYNTHETIC_CLOUD_ID,
            "--access-token",
            access_token,
            "--previous-snapshot",
            str(previous_snapshot),
            "--output-dir",
            str(output_dir),
            "--request-delay",
            "0",
            "--max-retries",
            "0",
            "--timeout",
            "5",
        ],
        client=_full_fixture_client(),
        now=lambda: "2026-05-15T04:00:00Z",
    )
    captured = capsys.readouterr()
    cli_text = captured.out + captured.err

    assert exit_code == 0
    assert "status=completed" in cli_text
    assert access_token not in cli_text
    for sensitive_word in SENSITIVE_WORDS[:3]:
        assert sensitive_word not in cli_text
        assert sensitive_word not in _all_output_text(output_dir)


def test_mvp_excluded_capabilities_remain_status_only(tmp_path: Path) -> None:
    config = _config(
        tmp_path=tmp_path,
        previous_snapshot=_copy_previous_snapshot(tmp_path),
    )

    result = run_data_sync_workflow(
        config=config,
        client=_full_fixture_client(),
        now=lambda: "2026-05-15T04:00:00Z",
    )

    assert result.message_payloads
    assert not (config.output_dir / "rabbitmq").exists()
    assert not (config.output_dir / "qdrant").exists()
    assert not (config.output_dir / "mongodb").exists()
    assert all(
        document.metadata["attachment_processing_status"] == "not_supported_in_mvp"
        for document in result.changed_documents
    )
