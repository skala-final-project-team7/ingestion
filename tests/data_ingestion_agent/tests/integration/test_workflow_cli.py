from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

from data_ingestion_agent.app.cli import run_cli
from data_ingestion_agent.config import DataIngestionConfig
from data_ingestion_agent.confluence import ConfluenceApiError
from data_ingestion_agent.graph import (
    build_data_ingestion_workflow,
    is_langgraph_available,
)
from data_ingestion_agent.workflow import run_full_crawl_workflow


class FakeConfluenceClient:
    def __init__(
        self,
        *,
        spaces: list[dict],
        descendants_by_homepage: dict[str, list[dict]],
        details_by_page: dict[str, dict | Exception],
    ) -> None:
        self.spaces = spaces
        self.descendants_by_homepage = descendants_by_homepage
        self.details_by_page = details_by_page
        self.calls: list[str] = []

    def list_spaces(self) -> list[dict]:
        self.calls.append("list_spaces")
        return self.spaces

    def list_page_descendants(self, homepage_id: str) -> list[dict]:
        self.calls.append(f"list_page_descendants:{homepage_id}")
        return self.descendants_by_homepage.get(homepage_id, [])

    def get_page_detail(self, page_id: str) -> dict:
        self.calls.append(f"get_page_detail:{page_id}")
        detail_or_error = self.details_by_page[page_id]
        if isinstance(detail_or_error, Exception):
            raise detail_or_error
        return detail_or_error


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _config(tmp_path: Path, *, access_token: str | None = None) -> DataIngestionConfig:
    return DataIngestionConfig(
        cloud_id=_runtime_value("cloud"),
        access_token=access_token or _runtime_value("token"),
        output_dir=tmp_path,
        request_delay_seconds=0,
    )


def _space(space_id: str = "space-001", homepage_id: str = "home-001") -> dict:
    return {
        "id": space_id,
        "key": "ENG",
        "name": "Engineering",
        "homepageId": homepage_id,
    }


def _page_ref(page_id: str = "page-001") -> dict:
    return {
        "id": page_id,
        "parentId": "parent-001",
        "depth": 1,
        "position": 0,
    }


def _page_detail(page_id: str = "page-001") -> dict:
    return {
        "id": page_id,
        "title": "Synthetic Runbook",
        "status": "current",
        "body": {"storage": {"value": "<h1>Runbook</h1><p>Restart</p>"}},
        "createdAt": "2026-05-14T00:00:00Z",
        "version": {"number": 1, "createdAt": "2026-05-14T01:00:00Z"},
        "_links": {"webui": f"/wiki/spaces/ENG/pages/{page_id}/Synthetic"},
    }


def test_workflow_processes_space_pages_and_writes_outputs(tmp_path: Path) -> None:
    client = FakeConfluenceClient(
        spaces=[_space()],
        descendants_by_homepage={"home-001": [_page_ref()]},
        details_by_page={"page-001": _page_detail()},
    )

    result = run_full_crawl_workflow(config=_config(tmp_path), client=client)

    assert client.calls == [
        "list_spaces",
        "list_page_descendants:home-001",
        "get_page_detail:page-001",
    ]
    assert result.report.status == "completed"
    assert result.report.counts.documents_written == 1
    assert result.write_result is not None
    assert result.write_result.documents_path.exists()
    document_line = result.write_result.documents_path.read_text(
        encoding="utf-8"
    ).strip()
    assert json.loads(document_line)["body"]["plain_text"] == "Runbook\nRestart"


def test_workflow_records_failed_item_and_finishes_partial_success(
    tmp_path: Path,
) -> None:
    client = FakeConfluenceClient(
        spaces=[_space()],
        descendants_by_homepage={"home-001": [_page_ref("page-001"), _page_ref("page-002")]},
        details_by_page={
            "page-001": _page_detail("page-001"),
            "page-002": ConfluenceApiError(
                status_code=403,
                error_type="permission_failure",
                message="Page detail request denied.",
                retryable=False,
                item_level=True,
                attempt_count=1,
            ),
        },
    )

    result = run_full_crawl_workflow(config=_config(tmp_path), client=client)

    assert result.report.status == "completed_with_errors"
    assert result.report.counts.documents_written == 1
    assert result.report.counts.failed_items == 1
    assert result.failed_items[0].item_id == "page-002"
    assert result.failed_items[0].retryable is False


def test_workflow_marks_job_failed_when_list_spaces_fails(tmp_path: Path) -> None:
    client = FakeConfluenceClient(
        spaces=[],
        descendants_by_homepage={},
        details_by_page={},
    )

    def fail_list_spaces() -> list[dict]:
        client.calls.append("list_spaces")
        raise ConfluenceApiError(
            status_code=401,
            error_type="auth_failure",
            message="Unauthorized.",
            retryable=False,
            item_level=False,
            attempt_count=1,
        )

    client.list_spaces = fail_list_spaces  # type: ignore[method-assign]

    result = run_full_crawl_workflow(config=_config(tmp_path), client=client)

    assert result.report.status == "failed"
    assert result.report.counts.failed_items == 1
    assert result.failed_items[0].error_type == "auth_failure"


def test_workflow_handles_empty_spaces(tmp_path: Path) -> None:
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FakeConfluenceClient(
            spaces=[],
            descendants_by_homepage={},
            details_by_page={},
        ),
    )

    assert result.report.status == "completed"
    assert result.report.counts.spaces == 0
    assert result.report.counts.documents_written == 0
    assert result.write_result is not None
    assert result.write_result.documents_path.read_text(encoding="utf-8") == ""


def test_workflow_handles_empty_pages(tmp_path: Path) -> None:
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FakeConfluenceClient(
            spaces=[_space()],
            descendants_by_homepage={"home-001": []},
            details_by_page={},
        ),
    )

    assert result.report.status == "completed"
    assert result.report.counts.spaces == 1
    assert result.report.counts.page_refs == 0
    assert result.report.counts.documents_written == 0


def test_cli_builds_config_writes_files_and_redacts_token(
    tmp_path: Path,
    capsys,
) -> None:
    access_token = _runtime_value("token")
    client = FakeConfluenceClient(
        spaces=[_space()],
        descendants_by_homepage={"home-001": [_page_ref()]},
        details_by_page={"page-001": _page_detail()},
    )

    exit_code = run_cli(
        [
            "--cloud-id",
            _runtime_value("cloud"),
            "--access-token",
            access_token,
            "--output-dir",
            str(tmp_path),
            "--request-delay",
            "0",
            "--max-retries",
            "1",
            "--timeout",
            "5",
        ],
        client=client,
    )

    captured = capsys.readouterr()
    combined_output = captured.out + captured.err

    assert exit_code == 0
    assert "documents_written=1" in captured.out
    assert access_token not in combined_output
    assert "Authorization" not in combined_output
    assert (tmp_path / "processed" / "documents.jsonl").exists()
    assert (tmp_path / "reports" / "ingestion_report.json").exists()
    assert (tmp_path / "failed" / "failed_items.jsonl").exists()


def test_langgraph_optional_fallback_is_explicit(tmp_path: Path) -> None:
    workflow = build_data_ingestion_workflow(
        config=_config(tmp_path),
        client=FakeConfluenceClient(
            spaces=[],
            descendants_by_homepage={},
            details_by_page={},
        ),
    )

    assert workflow.backend in {"langgraph", "sequential"}
    if not is_langgraph_available():
        assert workflow.backend == "sequential"


def test_script_module_exposes_cli_entrypoint() -> None:
    script_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "run_full_crawl.py"
    )
    spec = importlib.util.spec_from_file_location("run_full_crawl_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(module.main)
