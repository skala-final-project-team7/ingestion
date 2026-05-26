"""RAG 파이프라인 공통 열거형.

--------------------------------------------------
작성자 : 최태성
작성목적 : 본문 문서 유형·첨부 유형·질의 의도·검증 상태 등 파이프라인 전 단계가
          공유하는 열거형을 한 곳에서 정의한다 (설계서·청킹 전략 설계서 정합).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature1 schemas — 열거형 9종 정의 (enum.StrEnum 기반)
--------------------------------------------------
[호환성]
  - Python 3.11.x 권장 (enum.StrEnum 사용)
--------------------------------------------------
"""

from enum import StrEnum


class DocType(StrEnum):
    """본문 문서 유형 6종 (chunking-strategy.md §4)."""

    INCIDENT = "incident"
    OPERATION = "operation"
    FAQ = "faq"
    MEETING = "meeting"
    ADR = "adr"
    TROUBLESHOOT = "troubleshoot"


class AttachmentType(StrEnum):
    """첨부 파일 유형 (chunking-strategy.md §5)."""

    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    CSV = "csv"


class SourceType(StrEnum):
    """청크 출처 구분 — 검색 결과 출처 카드 분기."""

    PAGE = "page"
    ATTACHMENT = "attachment"


class ExtractedFormat(StrEnum):
    """첨부 텍스트 추출 형식 — Chunker 분기 신호."""

    RAW_TEXT = "raw_text"  # PDF / Word
    SHEET_SERIALIZED = "sheet_serialized"  # Excel / CSV


class Intent(StrEnum):
    """질의 라우터 4종 의도 (설계서 §4.4.5)."""

    INCIDENT_RESPONSE = "장애대응"
    OPERATION_GUIDE = "운영가이드"
    POLICY_PROCEDURE = "정책절차"
    HISTORY_LOOKUP = "이력조회"


class VerificationStatus(StrEnum):
    """답변 문장 검증 상태 (설계서 §4.7)."""

    PASS = "PASS"
    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class VerificationResult(StrEnum):
    """답변 전체의 집계 검증 결과 — SSE ``verification`` 이벤트 ``verificationResult``.

    문장별 ``VerificationStatus`` 를 BE 통합 스펙(``api-spec-BE-adjust.md`` §1-1)에 맞춰
    단일 값으로 집계한 결과다. ``PARTIALLY_SUPPORTED`` 는 문장 단위에는 없고 집계에만
    존재한다(``docs/api-spec.md`` "verification" 집계 규칙).
    """

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class IngestionStage(StrEnum):
    """Ingestion 처리 단계 — ingestion_jobs.stage (db-schema.md §2.3)."""

    ANALYZE = "analyze"
    CHUNK = "chunk"
    EMBED = "embed"
    UPSERT = "upsert"
    SYNC = "sync"


class IngestionStatus(StrEnum):
    """Ingestion 처리 결과 — 정상(SUCCESS) + 예외 코드 (chunking-strategy.md §8)."""

    SUCCESS = "SUCCESS"
    PARTIAL_PARSE = "PARTIAL_PARSE"
    EMPTY_BODY = "EMPTY_BODY"
    EMPTY_BODY_ATTACH_ONLY = "EMPTY_BODY_ATTACH_ONLY"
    INVALID_ACL = "INVALID_ACL"
    UNSUPPORTED_ATTACH_TYPE = "UNSUPPORTED_ATTACH_TYPE"
    ATTACH_ENCRYPTED = "ATTACH_ENCRYPTED"
    LOW_QUALITY_ATTACH = "LOW_QUALITY_ATTACH"
    ATTACH_NO_HEADER = "ATTACH_NO_HEADER"
    OVERSIZE_ATOMIC = "OVERSIZE_ATOMIC"
    TOKENIZER_FAIL = "TOKENIZER_FAIL"


class LlmModel(StrEnum):
    """LLM 모델 — 답변 생성(GPT-4o) / 보조(GPT-4o-mini: 라우터·검증·히스토리·문서분석기)."""

    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
