from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from data_ingestion_agent.config import DataIngestionConfig
from data_ingestion_agent.schemas import (
    AttachmentProcessingStatus,
    BodyContent,
    FailedItem,
    FailedItemStage,
    FailedItemType,
    IngestionReport,
    IngestionReportCounts,
    IngestionReportStatus,
    PageInfo,
    ProcessedDocument,
    ProcessedDocumentMetadata,
    SpaceInfo,
)


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _processed_document() -> ProcessedDocument:
    page = PageInfo(
        page_id="12345",
        parent_id=None,
        title="Synthetic Page",
        status="current",
        depth=0,
        child_position=0,
        page_url="https://example.invalid/wiki/spaces/ENG/pages/12345",
        created_at="2026-05-14T00:00:00Z",
        last_modified_at="2026-05-14T00:10:00Z",
        version_number=7,
    )

    return ProcessedDocument(
        job_id=_runtime_value("job"),
        cloud_id=_runtime_value("cloud"),
        space=SpaceInfo(
            space_id="space-1",
            space_key="ENG",
            space_name="Engineering",
        ),
        page=page,
        body=BodyContent(
            storage_html="<h1>Synthetic Page</h1>",
            plain_text="Synthetic Page",
        ),
        metadata=ProcessedDocumentMetadata(
            content_length=23,
            plain_text_length=14,
            has_attachments=False,
        ),
    )


def test_processed_document_schema_uses_canonical_defaults() -> None:
    document = _processed_document()

    assert document.document_id == "confluence-page-12345-7"
    assert document.source_type == "confluence_page"
    assert document.body.representation == "storage"
    assert (
        document.metadata.attachment_processing_status
        == AttachmentProcessingStatus.NOT_SUPPORTED_IN_MVP
    )

    serialized = document.to_dict()
    assert serialized["document_id"] == "confluence-page-12345-7"
    assert serialized["body"]["storage_html"] == "<h1>Synthetic Page</h1>"
    assert (
        serialized["metadata"]["attachment_processing_status"]
        == "not_supported_in_mvp"
    )


def test_processed_document_rejects_invalid_document_id() -> None:
    document = _processed_document()
    document.document_id = "unexpected"

    with pytest.raises(ValueError, match="document_id"):
        document.validate()


def test_failed_item_schema_validates_stage_and_attempt_count() -> None:
    failed_item = FailedItem(
        job_id=_runtime_value("job"),
        stage=FailedItemStage.FETCH_PAGE_DETAIL,
        item_type=FailedItemType.PAGE,
        item_id="12345",
        error_type="PermissionDenied",
        error_message="Page detail request was denied.",
        retryable=False,
        attempt_count=1,
    )

    serialized = failed_item.to_dict()

    assert serialized["stage"] == "fetch_page_detail"
    assert serialized["item_type"] == "page"
    assert serialized["status"] == "failed"
    assert serialized["attempt_count"] == 1

    with pytest.raises(ValueError, match="attempt_count"):
        FailedItem(
            job_id=_runtime_value("job"),
            stage=FailedItemStage.LIST_SPACES,
            item_type=FailedItemType.SPACE,
            item_id=None,
            error_type="BadRequest",
            error_message="Invalid request.",
            retryable=False,
            attempt_count=0,
        )


def test_ingestion_report_preserves_counts_and_output_paths() -> None:
    report = IngestionReport(
        job_id=_runtime_value("job"),
        status=IngestionReportStatus.COMPLETED_WITH_ERRORS,
        counts=IngestionReportCounts(
            spaces=2,
            page_refs=3,
            pages_fetched=2,
            documents_written=2,
            failed_items=1,
        ),
        output_paths={
            "documents": "data/processed/documents.jsonl",
            "failed": "data/failed/failed_items.jsonl",
            "report": "data/reports/report.json",
        },
    )

    serialized = report.to_dict()

    assert serialized["status"] == "completed_with_errors"
    assert serialized["counts"]["documents_written"] == 2
    assert serialized["output_paths"]["report"] == "data/reports/report.json"


def test_config_requires_external_cloud_id_token_and_output_dir(
    tmp_path: Path,
) -> None:
    config = DataIngestionConfig(
        cloud_id=_runtime_value("cloud"),
        access_token=_runtime_value("token"),
        output_dir=tmp_path,
    )

    assert config.output_dir == tmp_path
    assert config.request_delay_seconds == 0.3
    assert config.max_retries == 3
    assert config.timeout_seconds == 20

    with pytest.raises(ValueError, match="cloud_id"):
        DataIngestionConfig(
            cloud_id="",
            access_token=_runtime_value("token"),
            output_dir=tmp_path,
        )

    with pytest.raises(ValueError, match="access_token"):
        DataIngestionConfig(
            cloud_id=_runtime_value("cloud"),
            access_token="",
            output_dir=tmp_path,
        )


def test_config_serialization_redacts_access_token(tmp_path: Path) -> None:
    access_token = _runtime_value("token")
    config = DataIngestionConfig(
        cloud_id=_runtime_value("cloud"),
        access_token=access_token,
        output_dir=tmp_path,
    )

    serialized = config.to_safe_dict()

    assert serialized["access_token"] == "<redacted>"
    assert access_token not in repr(config)
    assert access_token not in str(serialized)
