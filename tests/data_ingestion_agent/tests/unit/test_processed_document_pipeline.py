from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from data_ingestion_agent.ingestion import (
    PageDetailMapper,
    build_failed_item,
    build_ingestion_report,
)
from data_ingestion_agent.schemas import (
    AttachmentProcessingStatus,
    FailedItemStage,
    FailedItemType,
    IngestionReportStatus,
    SpaceInfo,
)
from data_ingestion_agent.storage import LocalFileRepository


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _space() -> SpaceInfo:
    return SpaceInfo(
        space_id="space-001",
        space_key="ENG",
        space_name="Engineering",
    )


def _page_ref() -> dict[str, object]:
    return {
        "id": "page-001",
        "parentId": "parent-001",
        "depth": 2,
        "position": 4,
    }


def _page_detail(storage_html: str | None = "<h1>Runbook</h1><p>Restart</p>") -> dict:
    body: dict[str, object] = {}
    if storage_html is not None:
        body = {"storage": {"value": storage_html}}

    return {
        "id": "page-001",
        "title": "Synthetic Runbook",
        "status": "current",
        "parentId": "parent-001",
        "body": body,
        "createdAt": "2026-05-14T00:00:00Z",
        "version": {
            "number": 3,
            "createdAt": "2026-05-14T01:00:00Z",
        },
        "_links": {"webui": "/wiki/spaces/ENG/pages/page-001/Synthetic+Runbook"},
    }


def test_page_detail_maps_to_processed_document() -> None:
    job_id = _runtime_value("job")
    cloud_id = _runtime_value("cloud")
    mapper = PageDetailMapper()

    document = mapper.to_processed_document(
        job_id=job_id,
        cloud_id=cloud_id,
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail(),
    )

    assert document.document_id == "confluence-page-page-001-3"
    assert document.job_id == job_id
    assert document.cloud_id == cloud_id
    assert document.space.space_id == "space-001"
    assert document.space.space_key == "ENG"
    assert document.page.page_id == "page-001"
    assert document.page.parent_id == "parent-001"
    assert document.page.title == "Synthetic Runbook"
    assert document.page.page_url == "/wiki/spaces/ENG/pages/page-001/Synthetic+Runbook"
    assert document.page.created_at == "2026-05-14T00:00:00Z"
    assert document.page.last_modified_at == "2026-05-14T01:00:00Z"
    assert document.page.version_number == 3
    assert document.page.depth == 2
    assert document.page.child_position == 4


def test_storage_html_and_plain_text_are_preserved_separately() -> None:
    storage_html = "<h1>Runbook</h1><p>Restart&nbsp;service</p>"
    document = PageDetailMapper().to_processed_document(
        job_id=_runtime_value("job"),
        cloud_id=_runtime_value("cloud"),
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail(storage_html),
    )

    assert document.body.storage_html == storage_html
    assert document.body.plain_text == "Runbook\nRestart service"
    assert document.metadata.content_length == len(storage_html)
    assert document.metadata.plain_text_length == len(document.body.plain_text)


def test_missing_storage_body_is_safe() -> None:
    document = PageDetailMapper().to_processed_document(
        job_id=_runtime_value("job"),
        cloud_id=_runtime_value("cloud"),
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail(storage_html=None),
    )

    assert document.body.storage_html == ""
    assert document.body.plain_text == ""
    assert document.metadata.content_length == 0
    assert document.metadata.plain_text_length == 0


def test_attachment_status_is_marked_not_supported_in_mvp() -> None:
    document = PageDetailMapper().to_processed_document(
        job_id=_runtime_value("job"),
        cloud_id=_runtime_value("cloud"),
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail(),
    )

    assert document.metadata.has_attachments is False
    assert (
        document.metadata.attachment_processing_status
        == AttachmentProcessingStatus.NOT_SUPPORTED_IN_MVP
    )


def test_failed_item_helper_contains_page_reason_and_retryable() -> None:
    failed_item = build_failed_item(
        job_id=_runtime_value("job"),
        stage=FailedItemStage.FETCH_PAGE_DETAIL,
        item_type=FailedItemType.PAGE,
        item_id="page-001",
        error_type="permission_failure",
        error_message="Page detail request was denied.",
        retryable=False,
        attempt_count=1,
    )

    assert failed_item.item_id == "page-001"
    assert failed_item.error_type == "permission_failure"
    assert failed_item.error_message == "Page detail request was denied."
    assert failed_item.retryable is False


def test_report_helper_calculates_counts_from_pipeline_totals() -> None:
    report = build_ingestion_report(
        job_id=_runtime_value("job"),
        spaces_total=2,
        page_refs_total=5,
        pages_succeeded=3,
        documents_written=3,
        failed_items=1,
        skipped_items=1,
        output_paths={"documents": "processed/documents.jsonl"},
    )

    assert report.status == IngestionReportStatus.COMPLETED_WITH_ERRORS
    assert report.counts.spaces == 2
    assert report.counts.page_refs == 5
    assert report.counts.pages_fetched == 3
    assert report.counts.documents_written == 3
    assert report.counts.failed_items == 1


def test_local_writer_creates_json_and_jsonl_outputs(tmp_path: Path) -> None:
    job_id = _runtime_value("job")
    document = PageDetailMapper().to_processed_document(
        job_id=job_id,
        cloud_id=_runtime_value("cloud"),
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail(),
    )
    failed_item = build_failed_item(
        job_id=job_id,
        stage=FailedItemStage.TRANSFORM_HTML,
        item_type=FailedItemType.PAGE,
        item_id="page-002",
        error_type="TransformError",
        error_message="Synthetic transform failure.",
        retryable=False,
        attempt_count=1,
    )
    report = build_ingestion_report(
        job_id=job_id,
        spaces_total=1,
        page_refs_total=2,
        pages_succeeded=1,
        documents_written=1,
        failed_items=1,
        skipped_items=0,
    )

    result = LocalFileRepository(tmp_path).write_outputs(
        documents=[document],
        failed_items=[failed_item],
        report=report,
    )

    assert result.documents_path.exists()
    assert result.failed_items_path.exists()
    assert result.report_path.exists()
    assert result.output_paths["documents"].endswith("processed/documents.jsonl")
    assert result.output_paths["failed_items"].endswith("failed/failed_items.jsonl")
    assert result.output_paths["report"].endswith("reports/ingestion_report.json")

    document_lines = result.documents_path.read_text(encoding="utf-8").splitlines()
    failed_lines = result.failed_items_path.read_text(encoding="utf-8").splitlines()
    report_json = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert len(document_lines) == 1
    assert json.loads(document_lines[0])["document_id"] == "confluence-page-page-001-3"
    assert len(failed_lines) == 1
    assert json.loads(failed_lines[0])["item_id"] == "page-002"
    assert report_json["output_paths"]["documents"].endswith(
        "processed/documents.jsonl"
    )


def test_writer_output_does_not_include_sensitive_values(tmp_path: Path) -> None:
    sensitive_token = _runtime_value("sensitive")
    document = PageDetailMapper().to_processed_document(
        job_id=_runtime_value("job"),
        cloud_id=_runtime_value("cloud"),
        space=_space(),
        page_ref=_page_ref(),
        page_detail=_page_detail("<p>Visible body</p>"),
    )

    result = LocalFileRepository(tmp_path).write_outputs(
        documents=[document],
        failed_items=[],
        report=build_ingestion_report(
            job_id=document.job_id,
            spaces_total=1,
            page_refs_total=1,
            pages_succeeded=1,
            documents_written=1,
            failed_items=0,
            skipped_items=0,
        ),
    )

    combined_output = (
        result.documents_path.read_text(encoding="utf-8")
        + result.failed_items_path.read_text(encoding="utf-8")
        + result.report_path.read_text(encoding="utf-8")
    )

    assert sensitive_token not in combined_output
    assert "access_token" not in combined_output
    assert "Authorization" not in combined_output


def test_writer_creates_missing_directories(tmp_path: Path) -> None:
    output_root = tmp_path / "missing" / "outputs"

    result = LocalFileRepository(output_root).write_outputs(
        documents=[],
        failed_items=[],
        report=build_ingestion_report(
            job_id=_runtime_value("job"),
            spaces_total=0,
            page_refs_total=0,
            pages_succeeded=0,
            documents_written=0,
            failed_items=0,
            skipped_items=0,
        ),
    )

    assert (output_root / "processed").is_dir()
    assert (output_root / "failed").is_dir()
    assert (output_root / "reports").is_dir()
    assert result.documents_path.exists()
