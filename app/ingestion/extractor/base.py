"""첨부 텍스트 추출 인터페이스 (FR-002) [stub]."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.enums import AttachmentType, ExtractedFormat


@dataclass
class ExtractionResult:
    """첨부 1건의 텍스트 추출 결과."""

    attachment_id: str
    attachment_type: AttachmentType
    extracted_format: ExtractedFormat
    text: str
    # 추출 실패 시 False + reason (쿼리 전체 실패로 전파하지 않고 graceful degrade).
    ok: bool = True
    reason: str | None = None


def extract_attachment_text(
    *, attachment_id: str, attachment_type: AttachmentType, content: bytes
) -> ExtractionResult:
    """첨부 바이너리 → 텍스트 추출 (이미지·도형 제외).

    - PDF: PyMuPDF(fitz) 1차 → pdfplumber 폴백
    - Word(docx): python-docx 본문/표
    - Excel(xlsx)/CSV: openpyxl/csv → 시트 자연어 직렬화

    TODO(featureI-3): 유형별 추출 구현 + 실패 graceful degrade + attachment_texts 적재.
    """
    raise NotImplementedError("featureI-3에서 구현 — docs/ai/current-plan.md 참조")
