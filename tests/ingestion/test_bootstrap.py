"""Ingestion 부트스트랩(composition root) 단위 테스트 — PoC 모드 조립 검증.

실 어댑터 모드는 외부 인프라(E5/Qdrant/Mongo/OpenAI) 의존이라 통합 환경에서 검증하고,
여기서는 PoC 모드(전부 Fake) 조립과 raw_store 공유 선택 로직만 검증한다.
"""

from __future__ import annotations

from app.config import Settings
from app.ingestion.bootstrap import (
    build_chunking_worker_deps,
    build_document_analyzer,
    build_raw_page_store,
)
from app.storage.jobs import FakeIngestionJobsRepository
from app.storage.mongo_cache import FakeEmbeddingCache
from app.storage.qdrant_fake import FakeQdrantPoolStore
from app.storage.raw_store import FakeRawPageStore


def _poc_settings() -> Settings:
    return Settings(use_real_adapters=False)


def test_build_raw_page_store_poc_returns_fake() -> None:
    assert isinstance(build_raw_page_store(_poc_settings()), FakeRawPageStore)


def test_build_document_analyzer_poc_returns_none() -> None:
    # PoC 는 LLM 비용 0 — chunk_page 라벨 폴백을 사용한다.
    assert build_document_analyzer(_poc_settings()) is None


def test_build_chunking_worker_deps_poc_wires_fakes() -> None:
    deps = build_chunking_worker_deps(_poc_settings())

    assert isinstance(deps.raw_store, FakeRawPageStore)
    assert isinstance(deps.store, FakeQdrantPoolStore)
    assert isinstance(deps.cache, FakeEmbeddingCache)
    assert isinstance(deps.jobs, FakeIngestionJobsRepository)
    assert deps.doc_type_resolver is None


def test_build_chunking_worker_deps_shares_provided_raw_store() -> None:
    shared = FakeRawPageStore()

    deps = build_chunking_worker_deps(_poc_settings(), raw_store=shared)

    # crawl 과 worker 가 같은 raw_store 인스턴스를 공유하도록 주입 가능(in-process PoC).
    assert deps.raw_store is shared
