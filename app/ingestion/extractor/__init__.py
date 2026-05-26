"""app.ingestion.extractor — 첨부 파일 텍스트 추출기 (FR-002) [stub].

raw_attachments 의 PDF/Word/Excel 바이너리에서 텍스트를 추출(이미지·도형 제외)해
attachment_texts(MongoDB)에 적재하고 Chunking Queue(첨부)로 발행한다. Excel/CSV는 시트를
자연어로 직렬화해 LLM이 수치 맥락을 이해하도록 가공한다.

구현은 featureI-3 (docs/ai/current-plan.md). 추출 1차/폴백 라이브러리는 pyproject `[ingestion]`
extras(pymupdf/pdfplumber/python-docx/openpyxl/pandas)를 따른다.
"""

from app.ingestion.extractor.base import ExtractionResult, extract_attachment_text

__all__ = ["ExtractionResult", "extract_attachment_text"]
