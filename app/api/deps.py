"""수집 HTTP API 의존성 부트스트랩 — 잡 저장소 + 크롤 러너 조립 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : ``POST /ml/ingest`` 라우트가 사용하는 의존성을 ``Settings`` 기반으로 조립한다.
          공급원 어댑터(``build_source_adapter`` — json_fixture | atlassian)를 startup 1회
          생성해 잡 간 재사용하고, 잡 수명주기 저장소(``InMemoryIngestJobStore``)와
          크롤 러너(in-process 합성 파이프라인)를 묶어 ``IngestDeps`` 로 제공한다.
작성일 : 2026-05-29 (api-spec v2.2.0 §2-2/§2-3 HTTP API)
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-29, 최초 작성 — build_ingest_deps (job_store + run_crawl). run_crawl 은
    ``run_poc_ingestion`` 으로 crawl→chunk→upsert 를 in-process 합성 실행한다(PoC 전부 fake
    스토어 격리). 운영 분산 모드(RabbitMQ 워커 발행)는 후속 확장 지점.
--------------------------------------------------
[호환성]
  - Python 3.11.x
--------------------------------------------------
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.adapters.base import DocumentSourceAdapter
from app.adapters.factory import build_source_adapter
from app.config import Settings
from app.ingestion.crawler import CrawlRequest, CrawlResult
from app.ingestion.pipeline import run_poc_ingestion
from app.storage.ingest_jobs import InMemoryIngestJobStore, IngestJobStore

# 크롤 러너 시그니처 — ``CrawlRequest`` 를 받아 집계 ``CrawlResult`` 를 돌려준다.
CrawlRunner = Callable[[CrawlRequest], CrawlResult]


@dataclass
class IngestDeps:
    """수집 HTTP API 의존성 묶음 — 라우트와 백그라운드 잡 태스크가 공유한다."""

    job_store: IngestJobStore
    run_crawl: CrawlRunner


def build_ingest_deps(settings: Settings) -> IngestDeps:
    """``Settings`` 로 수집 API 의존성을 조립한다.

    - 공급원 어댑터: ``Settings.source_type`` 에 따라 json_fixture(샘플) 또는 atlassian(실
      Confluence)을 startup 에서 1회 생성해 잡 간 재사용한다.
    - 크롤 러너: ``run_poc_ingestion`` 으로 crawl→chunk→upsert 전 체인을 in-process 로
      합성 실행한다(``app/ingestion/pipeline.py`` 의 PoC 합성 — fake 스토어로 격리). 운영
      분산 모드(crawl/worker 를 RabbitMQ 로 분리)는 본 러너만 교체하면 되도록 격리한다.

    Args:
        settings: 환경 설정(``source_type`` / ``samples_dir`` / 자격증명 등).

    Returns:
        잡 저장소 + 크롤 러너를 묶은 ``IngestDeps``.
    """
    source: DocumentSourceAdapter = build_source_adapter(settings)

    def _run_crawl(request: CrawlRequest) -> CrawlResult:
        # in-process 합성 파이프라인(crawl→chunk→upsert). PoC 는 전부 fake 스토어로 격리
        # 실행하므로 외부 컨테이너·모델 없이 동작한다. 반환 ``CrawlResult`` 로 잡 카운트를 채운다.
        result, _components = run_poc_ingestion(request, source)
        return result.crawl

    return IngestDeps(job_store=InMemoryIngestJobStore(), run_crawl=_run_crawl)
