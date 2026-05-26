"""Chunking+Embedding Worker 단위 테스트 — content.chunking → Qdrant upsert end-to-end.

실제 Adaptive Chunker(chunk_page) + indexer.index_chunks 를 구동하고, 외부 모델·Qdrant·
Mongo 는 Fake 로 대체한다(FakeDenseEmbedder/FakeSparseEmbedder/FakeEmbeddingCache +
in-memory fake Qdrant store). 멱등성·ACL/빈 본문 게이트·잡 기록을 검증한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.ingestion.embedder.base import (
    FakeDenseEmbedder,
    FakeSparseEmbedder,
    SparseVector,
)
from app.ingestion.vector_store import POOL_NAMES
from app.ingestion.workers.chunking_worker import (
    ChunkingWorkerDeps,
    RawPageNotFoundError,
    process_chunking_message,
    run_chunking_worker,
)
from app.ingestion.workers.consumer import FakeMessageConsumer
from app.schemas.chunk import Chunk
from app.schemas.enums import IngestionStage, IngestionStatus
from app.schemas.page_object import PageObject
from app.storage.jobs import FakeIngestionJobsRepository
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.raw_store import FakeRawPageStore


class _FakeQdrantStore:
    """index_chunks 가 호출하는 upsert_chunks_batch 만 캡처하는 in-memory fake."""

    def __init__(self) -> None:
        self.upserts: dict[str, list[str]] = {pool: [] for pool in POOL_NAMES}

    def upsert_chunks_batch(
        self,
        pool_name: str,
        items: Iterable[tuple[Chunk, int, list[float], SparseVector]],
    ) -> None:
        for chunk, _version, _dense, _sparse in items:
            self.upserts[pool_name].append(chunk.metadata.chunk_id)


def _page(page_id: str = "page-1", *, acl: bool = True, body: str | None = None) -> PageObject:
    body_html = (
        "<h2>Restart Procedure</h2>"
        "<p>Stop the service, clear the cache, then start it again and verify health.</p>"
        if body is None
        else body
    )
    return PageObject(
        page_id=page_id,
        space_key="ENG",
        title="Runbook",
        body_html=body_html,
        version_number=3,
        last_modified=datetime.fromisoformat("2026-05-14T01:00:00+00:00"),
        allowed_groups=["space:ENG"] if acl else [],
        allowed_users=[],
        webui_link=f"/wiki/{page_id}",
        labels=["operation"],
    )


def _deps(
    store: FakeRawPageStore,
    *,
    jobs: FakeIngestionJobsRepository | None = None,
    cache: FakeEmbeddingCache | None = None,
    qdrant: _FakeQdrantStore | None = None,
):
    return ChunkingWorkerDeps(
        raw_store=store,
        dense_embedder=FakeDenseEmbedder(),
        sparse_embedder=FakeSparseEmbedder(),
        store=qdrant or _FakeQdrantStore(),
        cache=cache or FakeEmbeddingCache(),
        jobs=jobs,
    )


def test_process_message_chunks_embeds_and_upserts() -> None:
    raw = FakeRawPageStore()
    raw.save_page(_page("page-1"))
    qdrant = _FakeQdrantStore()
    jobs = FakeIngestionJobsRepository()
    deps = _deps(raw, jobs=jobs, qdrant=qdrant)

    result = process_chunking_message({"page_id": "page-1"}, deps)

    assert result.status is IngestionStatus.SUCCESS
    assert result.chunks >= 1
    assert result.upserted == result.chunks
    # 3개 Pool 모두에 동일 청크 수가 upsert 된다.
    for pool in POOL_NAMES:
        assert len(qdrant.upserts[pool]) == result.chunks
    # ingestion_jobs 에 upsert 단계 SUCCESS 1건 기록.
    assert len(jobs.records) == 1
    assert jobs.records[0].stage is IngestionStage.UPSERT
    assert jobs.records[0].status is IngestionStatus.SUCCESS
    assert jobs.records[0].page_id == "page-1"


def test_reindex_same_version_is_idempotent_skip() -> None:
    raw = FakeRawPageStore()
    raw.save_page(_page("page-1"))
    cache = FakeEmbeddingCache()
    deps_first = _deps(raw, cache=cache)
    first = process_chunking_message({"page_id": "page-1"}, deps_first)
    assert first.upserted >= 1

    # 같은 cache 재사용 → 동일 (chunk_id, version) 캐시 히트로 스킵.
    deps_second = _deps(raw, cache=cache)
    second = process_chunking_message({"page_id": "page-1"}, deps_second)
    assert second.status is IngestionStatus.SUCCESS
    assert second.upserted == 0
    assert second.skipped == first.upserted


def test_acl_missing_page_is_not_indexed() -> None:
    raw = FakeRawPageStore()
    raw.save_page(_page("page-1", acl=False))
    qdrant = _FakeQdrantStore()
    jobs = FakeIngestionJobsRepository()
    deps = _deps(raw, jobs=jobs, qdrant=qdrant)

    result = process_chunking_message({"page_id": "page-1"}, deps)

    assert result.status is IngestionStatus.INVALID_ACL
    assert result.upserted == 0
    assert all(qdrant.upserts[pool] == [] for pool in POOL_NAMES)
    assert jobs.records[0].status is IngestionStatus.INVALID_ACL


def test_empty_body_page_yields_empty_body_status() -> None:
    raw = FakeRawPageStore()
    raw.save_page(_page("page-1", body=""))
    deps = _deps(raw)

    result = process_chunking_message({"page_id": "page-1"}, deps)

    assert result.status is IngestionStatus.EMPTY_BODY
    assert result.chunks == 0


def test_missing_raw_page_raises() -> None:
    deps = _deps(FakeRawPageStore())
    try:
        process_chunking_message({"page_id": "ghost"}, deps)
    except RawPageNotFoundError as exc:
        assert "ghost" in str(exc)
    else:  # pragma: no cover - 실패 시에만
        raise AssertionError("RawPageNotFoundError expected")


def test_run_worker_processes_all_consumer_messages() -> None:
    raw = FakeRawPageStore()
    raw.save_page(_page("page-1"))
    raw.save_page(_page("page-2"))
    consumer = FakeMessageConsumer(messages=[{"page_id": "page-1"}, {"page_id": "page-2"}])
    deps = _deps(raw)

    results = run_chunking_worker(consumer, deps)

    assert [r.page_id for r in results] == ["page-1", "page-2"]
    assert all(r.status is IngestionStatus.SUCCESS for r in results)
