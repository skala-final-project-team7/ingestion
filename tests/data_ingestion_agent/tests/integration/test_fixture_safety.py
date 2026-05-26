from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from data_ingestion_agent.app.cli import run_cli
from data_ingestion_agent.config import DataIngestionConfig
from data_ingestion_agent.confluence import ConfluenceApiError
from data_ingestion_agent.workflow import run_full_crawl_workflow

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "confluence_synthetic.json"
FORBIDDEN_OUTPUT_MARKERS = (
    "Authorization",
    "access_token",
    "Bearer ",
    "synthetic-runtime-credential",
)


class FixtureConfluenceClient:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture

    def list_spaces(self) -> list[dict]:
        return self.fixture["spaces"]

    def list_page_descendants(self, homepage_id: str) -> list[dict]:
        return self.fixture["descendants_by_homepage"].get(homepage_id, [])

    def get_page_detail(self, page_id: str) -> dict:
        failure = self.fixture["page_detail_failures"].get(page_id)
        if failure:
            raise ConfluenceApiError(
                status_code=failure["status_code"],
                error_type=failure["error_type"],
                message=failure["message"],
                retryable=failure["retryable"],
                item_level=failure["item_level"],
                attempt_count=failure["attempt_count"],
            )
        return self.fixture["page_details"][page_id]


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _config(tmp_path: Path) -> DataIngestionConfig:
    return DataIngestionConfig(
        cloud_id=_runtime_value("cloud"),
        access_token="synthetic-runtime-credential-value",
        output_dir=tmp_path,
        request_delay_seconds=0,
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _all_output_text(output_root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(output_root.rglob("*"))
        if path.is_file()
    )


def test_fixture_does_not_contain_realistic_secret_markers() -> None:
    fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

    assert "Authorization" not in fixture_text
    assert "access_token" not in fixture_text
    assert "Bearer " not in fixture_text
    assert "cloud_id" not in fixture_text
    assert "sk-" not in fixture_text
    assert "xoxb-" not in fixture_text


def test_fixture_full_workflow_writes_expected_files_and_shapes(tmp_path: Path) -> None:
    fixture = _load_fixture()

    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FixtureConfluenceClient(fixture),
    )

    assert result.write_result is not None
    documents_path = result.write_result.documents_path
    failed_items_path = result.write_result.failed_items_path
    report_path = result.write_result.report_path
    assert documents_path.exists()
    assert failed_items_path.exists()
    assert report_path.exists()

    documents = _read_jsonl(documents_path)
    failed_items = _read_jsonl(failed_items_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert len(documents) == 3
    assert len(failed_items) == 2
    assert report["status"] == "completed_with_errors"
    assert report["counts"] == {
        "spaces": 4,
        "page_refs": 4,
        "pages_fetched": 3,
        "documents_written": 3,
        "failed_items": 2,
    }

    required_document_keys = {
        "document_id",
        "job_id",
        "source_type",
        "cloud_id",
        "space",
        "page",
        "body",
        "metadata",
    }
    for document in documents:
        assert required_document_keys.issubset(document)
        assert document["source_type"] == "confluence_page"
        assert document["body"]["representation"] == "storage"
        assert "storage_html" in document["body"]
        assert "plain_text" in document["body"]
        assert (
            document["metadata"]["attachment_processing_status"]
            == "not_supported_in_mvp"
        )


def test_fixture_preserves_storage_html_and_plain_text(tmp_path: Path) -> None:
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FixtureConfluenceClient(_load_fixture()),
    )
    assert result.write_result is not None
    documents = _read_jsonl(result.write_result.documents_path)
    document_by_id = {document["page"]["page_id"]: document for document in documents}

    runbook = document_by_id["page-runbook"]
    assert "<h1>Synthetic Runbook</h1>" in runbook["body"]["storage_html"]
    assert "Synthetic Runbook\nRestart the synthetic service." in runbook["body"]["plain_text"]

    macro_page = document_by_id["page-macro-attachment"]
    assert "ac:structured-macro" in macro_page["body"]["storage_html"]
    assert "Synthetic macro body" in macro_page["body"]["plain_text"]
    assert macro_page["metadata"]["has_attachments"] is False
    assert (
        macro_page["metadata"]["attachment_processing_status"]
        == "not_supported_in_mvp"
    )


def test_partial_failure_fixture_records_failed_page(tmp_path: Path) -> None:
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FixtureConfluenceClient(_load_fixture()),
    )
    assert result.write_result is not None
    failed_items = _read_jsonl(result.write_result.failed_items_path)
    failed_by_id = {failed_item["item_id"]: failed_item for failed_item in failed_items}

    assert failed_by_id["page-denied"]["stage"] == "fetch_page_detail"
    assert failed_by_id["page-denied"]["error_type"] == "permission_failure"
    assert failed_by_id["space-missing-home"]["stage"] == "collect_page_tree"


def test_empty_space_and_empty_page_fixture_do_not_raise(tmp_path: Path) -> None:
    fixture = _load_fixture()
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FixtureConfluenceClient(fixture),
    )

    assert result.report.counts.spaces == 4
    assert result.report.counts.page_refs == 4
    assert result.report.counts.documents_written == 3


def test_outputs_do_not_contain_runtime_credential_or_auth_markers(tmp_path: Path) -> None:
    runtime_credential = "synthetic-runtime-credential-value"
    result = run_full_crawl_workflow(
        config=DataIngestionConfig(
            cloud_id=_runtime_value("cloud"),
            access_token=runtime_credential,
            output_dir=tmp_path,
            request_delay_seconds=0,
        ),
        client=FixtureConfluenceClient(_load_fixture()),
    )

    assert result.write_result is not None
    output_text = _all_output_text(tmp_path)
    for marker in FORBIDDEN_OUTPUT_MARKERS:
        assert marker not in output_text


def test_cli_fixture_run_redacts_runtime_credential_and_writes_outputs(
    tmp_path: Path,
    capsys,
) -> None:
    runtime_credential = "synthetic-runtime-credential-value"
    exit_code = run_cli(
        [
            "--cloud-id",
            _runtime_value("cloud"),
            "--access-token",
            runtime_credential,
            "--output-dir",
            str(tmp_path),
            "--request-delay",
            "0",
        ],
        client=FixtureConfluenceClient(_load_fixture()),
    )

    captured = capsys.readouterr()
    combined_output = captured.out + captured.err

    assert exit_code == 0
    assert "documents_written=3" in captured.out
    assert runtime_credential not in combined_output
    assert "Authorization" not in combined_output
    assert (tmp_path / "processed" / "documents.jsonl").exists()
    assert (tmp_path / "failed" / "failed_items.jsonl").exists()
    assert (tmp_path / "reports" / "ingestion_report.json").exists()


def test_long_and_empty_body_boundary_fixture(tmp_path: Path) -> None:
    fixture = _load_fixture()
    result = run_full_crawl_workflow(
        config=_config(tmp_path),
        client=FixtureConfluenceClient(fixture["boundary_empty_and_long"]),
    )

    assert result.write_result is not None
    documents = _read_jsonl(result.write_result.documents_path)
    document_by_id = {document["page"]["page_id"]: document for document in documents}

    assert document_by_id["page-empty-body"]["body"]["storage_html"] == ""
    assert document_by_id["page-empty-body"]["body"]["plain_text"] == ""
    assert document_by_id["page-long-body"]["metadata"]["plain_text_length"] > 1000
