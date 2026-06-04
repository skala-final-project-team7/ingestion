"""수집 HTTP API 라우트 회귀 — POST /ml/ingest + status + health.

본 테스트는 api-spec v2.2.0 §2-2/§2-3/§2-4-2 계약을 검증한다.
- POST /ml/ingest → jobId 발급 + status=STARTED + startedAt(KST), 백그라운드 크롤 후 COMPLETED.
- GET /ml/ingest/status/{jobId} → jobId/status/totalPages/processedPages/failedPages/startedAt.
- GET /ml/ingest/health → {"status": "UP"}.

크롤 러너는 stub 으로 주입해(외부 컨테이너·샘플 파일 의존 없이) 잡 카운트 집계만 결정론적으로
검증한다. ASGITransport 는 응답 완료 전에 BackgroundTasks 를 끝내므로 POST 직후 상태 조회 시
이미 COMPLETED 다.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import IngestDeps
from app.api.main import create_app
from app.api.routes import get_deps
from app.ingestion.crawler import CrawlRequest, CrawlResult
from app.ingestion.workers.sync_worker import SyncWorker, SyncWorkerDeps
from app.storage.ingest_jobs import InMemoryIngestJobStore
from app.storage.qdrant_fake import FakeQdrantPoolStore


def _stub_deps() -> IngestDeps:
    """stub 크롤 러너(3 성공 + 1 실패)를 가진 IngestDeps — 카운트 집계 결정론."""

    def _run_crawl(request: CrawlRequest) -> CrawlResult:
        return CrawlResult(
            space_key=request.space_key,
            pages_collected=3,
            failed_page_ids=["p-bad"],
        )

    return IngestDeps(
        job_store=InMemoryIngestJobStore(),
        run_crawl=_run_crawl,
        sync_worker=SyncWorker(SyncWorkerDeps(store=FakeQdrantPoolStore())),
    )


def _client(deps: IngestDeps) -> httpx.AsyncClient:
    """ASGITransport 클라이언트 — get_deps 를 stub 으로 override(lifespan 우회)."""
    app = create_app()
    app.dependency_overrides[get_deps] = lambda: deps
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_ingest_health_returns_up() -> None:
    """api-spec v2.2.0 §2-4-2 — GET /ml/ingest/health → {"status": "UP"}."""
    async with _client(_stub_deps()) as client:
        resp = await client.get("/ml/ingest/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "UP"}


@pytest.mark.asyncio
async def test_ingest_trigger_then_status_completed() -> None:
    """POST /ml/ingest → STARTED + jobId, 백그라운드 완료 후 status=COMPLETED + 카운트 집계."""
    deps = _stub_deps()
    async with _client(deps) as client:
        # api-spec v2.4.0 §2-2 — spaceKey 없음. mode 만(또는 빈 본문)으로 전체 스페이스 수집.
        resp = await client.post("/ml/ingest", json={"mode": "full"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "STARTED"
        assert body["jobId"].startswith("job-")
        assert body["startedAt"].endswith("+09:00")  # KST 절대 전환
        job_id = body["jobId"]

        # ASGITransport 는 응답 완료 전 BackgroundTasks 를 끝내므로 이미 COMPLETED.
        status_resp = await client.get(f"/ml/ingest/status/{job_id}")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["jobId"] == job_id
    assert status["status"] == "COMPLETED"
    assert status["totalPages"] == 4  # 3 성공 + 1 실패
    assert status["processedPages"] == 3
    assert status["failedPages"] == 1
    assert status["startedAt"].endswith("+09:00")


@pytest.mark.asyncio
async def test_ingest_status_unknown_job_returns_404_envelope() -> None:
    """존재하지 않는 jobId → 4필드 에러 봉투(isSuccess/code/errorCode/message)로 404."""
    async with _client(_stub_deps()) as client:
        resp = await client.get("/ml/ingest/status/job-does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body == {
        "isSuccess": False,
        "code": 404,
        "errorCode": "RESOURCE_NOT_FOUND",
        "message": "수집 작업을 찾을 수 없습니다: job-does-not-exist",
    }


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_mode() -> None:
    """mode 는 full | delta 만 허용 — 그 외 값은 422(Pydantic 검증)."""
    async with _client(_stub_deps()) as client:
        resp = await client.post("/ml/ingest", json={"mode": "bogus"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_accepts_empty_body_no_space_key() -> None:
    """api-spec v2.4.0 §2-2 — spaceKey 제거. 빈 본문도 허용(mode 기본 full, 전체 스페이스 수집)."""
    async with _client(_stub_deps()) as client:
        resp = await client.post("/ml/ingest", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "STARTED"
