"""Chunking + Embedding Worker (FR-003/FR-004) — content.chunking 소비 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : Data Ingestion Agent(FR-001)가 발행한 ``content.chunking`` 메시지를 소비해
          ``raw_pages`` 본문을 로드 → Adaptive Chunker(chunk_page) → Dual Embedding +
          Multi-Pool Qdrant upsert(index_chunks, embedding_cache 멱등성)까지 한 흐름으로
          처리한다. 복사 자산(chunker/embedder/indexer)이 이미 embed+upsert 를 결합하므로
          PoC 는 단일 Worker 토폴로지로 배선한다(featureI-4 결정 — content.embedding 큐는
          상수로 예약). 단계 결과는 ``ingestion_jobs`` 에 기록한다(`docs/db-schema.md` §2.3).
작성일 : 2026-05-26 (featureI-4)
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-26, featureI-4, 단일 Chunking+Embedding Worker 배선 — process_chunking_message
    (raw_pages 로드 → chunk_page → index_chunks → ingestion_jobs) + run_chunking_worker 루프.
--------------------------------------------------
구현 메모(featureI-4):
  - 외부 의존성(임베더/Qdrant/cache/raw_store/jobs)은 주입 가능하게 둔다(테스트는 Fake).
    실 어댑터(E5/BM25/Qdrant from_settings) 부트스트랩은 배포 wiring(후속).
  - doc_type 은 chunk_page 의 라벨 휴리스틱 폴백을 사용한다. GPT-4o-mini 문서 분석기[Agent]
    는 featureI-4b 후속. 첨부 청크 경로는 첨부 입력이 생기는 FR-002(featureI-3) 이후 연결.
  - ACL 누락 페이지는 색인하지 않는다(INVALID_ACL — app/CLAUDE.md §3). 토큰·자격증명은
    메시지·로그에 남기지 않는다.
--------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.ingestion.chunker import chunk_page
from app.ingestion.indexer import index_chunks
from app.ingestion.workers.consumer import MessageConsumer
from app.schemas.enums import IngestionStage, IngestionStatus
from app.storage.jobs import IngestionJobRecord, IngestionJobsRepository
from app.storage.raw_store import RawPageStore


class RawPageNotFoundError(KeyError):
    """``content.chunking`` 메시지의 ``page_id`` 가 ``raw_pages`` 에 없을 때(파이프라인 불일치)."""


@dataclass(slots=True)
class ChunkingWorkerDeps:
    """Chunking Worker 가 사용하는 주입 의존성 묶음.

    Attributes:
        raw_store: ``raw_pages`` 조회 어댑터(``get_page``).
        dense_embedder / sparse_embedder: Dual Embedding 어댑터(테스트는 Fake).
        store: Qdrant Multi-Pool 저장소(``index_chunks`` 가 upsert).
        cache: ``embedding_cache`` 어댑터(멱등성).
        jobs: ``ingestion_jobs`` 기록 어댑터. None 이면 기록을 생략한다.
        chunk_lookup: ``chunk_lookup`` 어댑터. None 이면 적재를 생략한다(legacy 호환).
        doc_type_resolver: 문서 분석기[Agent](`DocumentAnalyzer`). 주입 시 스페이스 단위
            LLM doc_type 으로 청킹하고, None 이면 chunk_page 의 라벨 휴리스틱 폴백을 쓴다.
    """

    raw_store: RawPageStore
    dense_embedder: Any
    sparse_embedder: Any
    store: Any
    cache: Any
    jobs: IngestionJobsRepository | None = None
    chunk_lookup: Any | None = None
    doc_type_resolver: Any | None = None


@dataclass(slots=True)
class ChunkingMessageResult:
    """``content.chunking`` 메시지 1건 처리 결과."""

    page_id: str
    status: IngestionStatus
    chunks: int = 0
    upserted: int = 0
    skipped: int = 0


def process_chunking_message(
    message: dict[str, Any], deps: ChunkingWorkerDeps
) -> ChunkingMessageResult:
    """``content.chunking`` 메시지 1건을 청킹·임베딩·색인한다(단위 처리 — 테스트 대상).

    흐름: raw_pages 로드 → ACL 게이트(INVALID_ACL) → chunk_page(폴백 doc_type) →
    빈 본문 게이트(EMPTY_BODY) → index_chunks(embed+upsert+cache) → ingestion_jobs(SUCCESS).

    Args:
        message: 발행 메시지. ``page_id`` 필수(`build_chunking_message` 형식).
        deps: 주입 의존성 묶음.

    Returns:
        처리 결과(`ChunkingMessageResult`) — status 는 SUCCESS / INVALID_ACL / EMPTY_BODY.

    Raises:
        RawPageNotFoundError: ``page_id`` 가 ``raw_pages`` 에 없을 때(상위에서 DLQ 처리).
    """
    page_id = str(message["page_id"])
    started_at = datetime.now(UTC)

    page = deps.raw_store.get_page(page_id)
    if page is None:
        raise RawPageNotFoundError(page_id)

    # ACL 게이트 — allowed_groups/users 가 모두 비면 색인하지 않는다(app/CLAUDE.md §3).
    if page.is_acl_missing:
        _record(deps, page_id, IngestionStatus.INVALID_ACL, started_at, error="ACL missing")
        return ChunkingMessageResult(page_id=page_id, status=IngestionStatus.INVALID_ACL)

    # doc_type: 분석기[Agent] 주입 시 스페이스 단위 LLM 판별, 미주입 시 라벨 휴리스틱 폴백.
    doc_type = (
        deps.doc_type_resolver.resolve_doc_type(page)
        if deps.doc_type_resolver is not None
        else None
    )
    chunks = chunk_page(page, doc_type)
    if not chunks:
        _record(deps, page_id, IngestionStatus.EMPTY_BODY, started_at, error="no chunks")
        return ChunkingMessageResult(page_id=page_id, status=IngestionStatus.EMPTY_BODY)

    result = index_chunks(
        chunks,
        version_by_page_id={page.page_id: page.version_number},
        dense_embedder=deps.dense_embedder,
        sparse_embedder=deps.sparse_embedder,
        store=deps.store,
        cache=deps.cache,
        chunk_lookup=deps.chunk_lookup,
    )
    _record(deps, page_id, IngestionStatus.SUCCESS, started_at, error=None)
    return ChunkingMessageResult(
        page_id=page_id,
        status=IngestionStatus.SUCCESS,
        chunks=len(chunks),
        upserted=result.upserted_count,
        skipped=result.skipped_count,
    )


def run_chunking_worker(
    consumer: MessageConsumer, deps: ChunkingWorkerDeps
) -> list[ChunkingMessageResult]:
    """consumer 스트림의 ``content.chunking`` 메시지를 순서대로 처리한다(얇은 루프).

    각 메시지를 ``process_chunking_message`` 로 처리하고 결과를 모아 반환한다. 운영에서는
    pika consumer 가 ack/DLQ 를 관리하고, 처리 핵심 로직은 단위 함수에 위임된다.
    """
    return [process_chunking_message(message, deps) for message in consumer.consume()]


def _record(
    deps: ChunkingWorkerDeps,
    page_id: str,
    status: IngestionStatus,
    started_at: datetime,
    *,
    error: str | None,
) -> None:
    """``ingestion_jobs`` 에 단계 결과를 기록한다(jobs 미주입 시 noop).

    단일 Worker 가 chunk→embed→upsert 를 결합 처리하므로 색인 시도의 종단 단계인
    ``upsert`` 로 1건 기록한다(stage enum 정합 — db-schema §2.3).
    """
    if deps.jobs is None:
        return
    deps.jobs.record(
        IngestionJobRecord(
            page_id=page_id,
            attachment_id=None,
            stage=IngestionStage.UPSERT,
            status=status,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            error=error,
        )
    )
