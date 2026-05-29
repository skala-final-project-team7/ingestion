"""수집 HTTP API 라우트 — POST /ml/ingest + status + health [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : api-spec v2.2.0 §2-2/§2-3/§2-4-2 의 수집(Data Ingestion) HTTP 계약을 제공한다.
          ``POST /ml/ingest`` 는 잡을 생성(``STARTED``)하고 백그라운드에서 crawl→chunk→
          upsert 를 실행하며, ``GET /ml/ingest/status/{jobId}`` 가 진행 상태·집계 카운트를,
          ``GET /ml/ingest/health`` 가 서버 가용성을 반환한다. 응답은 BFF 가 공통 Wrapper 로
          감싸므로 ML 은 **data 객체를 그대로(unwrapped)** 반환한다(§2-3 "외부 API data 동일",
          §2-4 health 선례와 정합).
작성일 : 2026-05-29 (api-spec v2.2.0 §2-2/§2-3/§2-4-2)
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-29, 최초 작성 — IngestRequest(spaceKey/mode/accessToken/cloudId) + POST 트리거
    (BackgroundTasks 로 비동기 크롤) + status 조회(KST startedAt) + health.
--------------------------------------------------
[보안] 요청 ``accessToken``/``cloudId`` 는 로그·응답 본문에 남기지 않는다(루트 CLAUDE.md
       보안 규칙). 상태 응답에도 토큰 관련 필드를 포함하지 않는다.
[호환성]
  - Python 3.11.x, FastAPI 0.111+
--------------------------------------------------
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import IngestDeps
from app.ingestion.crawler import CrawlRequest
from app.schemas.enums import IngestJobStatus

_LOGGER = logging.getLogger(__name__)

# api-spec "시간 표기 정책" — 응답 timestamp 는 KST(+09:00)로 절대 전환해 반환한다.
_KST = timezone(timedelta(hours=9))

# 허용 수집 모드(api-spec §2-2). 2단계 PoC 는 둘 다 full crawl 합성 파이프라인으로 처리하며,
# delta(변경분) 의 sync 에이전트 배선은 후속이다.
_ALLOWED_MODES: frozenset[str] = frozenset({"full", "delta"})

router = APIRouter()


def _to_kst(dt: datetime) -> str:
    """UTC(또는 naive) datetime 을 KST(+09:00) ISO 8601 문자열로 변환한다."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_KST).isoformat()


class IngestRequest(BaseModel):
    """``POST /ml/ingest`` 요청 본문 (api-spec v2.2.0 §2-2).

    BFF 는 camelCase JSON(``spaceKey``/``accessToken``/``cloudId``)을 보낸다.
    ``populate_by_name=True`` 로 snake_case 입력도 허용한다(테스트 편의).
    """

    model_config = ConfigDict(populate_by_name=True)

    space_key: str = Field(..., min_length=1, alias="spaceKey", description="수집 대상 스페이스 키")
    mode: str = Field(default="full", description="수집 모드 — full(전체) | delta(변경분)")
    access_token: str | None = Field(
        default=None, alias="accessToken", description="Confluence OAuth access token(PoC, 로그 금지)"
    )
    cloud_id: str | None = Field(default=None, alias="cloudId", description="Confluence cloudId")

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        """mode 는 ``full`` | ``delta`` 만 허용한다(api-spec §2-2)."""
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_MODES:
            raise ValueError(f"mode 는 full | delta 여야 합니다 (받음: {value!r})")
        return normalized


def get_deps(request: Request) -> IngestDeps:
    """FastAPI Depends — lifespan 에서 만든 수집 의존성을 반환한다.

    테스트는 ``app.dependency_overrides[get_deps] = lambda: fake_deps`` 로 교체할 수 있다.
    """
    return request.app.state.ingest_deps


IngestDepsDep = Annotated[IngestDeps, Depends(get_deps)]


def _run_ingest_job(deps: IngestDeps, job_id: str, crawl_request: CrawlRequest) -> None:
    """백그라운드 수집 잡 — 상태를 ``IN_PROGRESS`` 로 올리고 크롤 실행 후 마감한다.

    크롤 성공 시 ``CrawlResult`` 집계로 카운트를 채워 ``COMPLETED`` 로, 예외 시 ``FAILED``
    로 마감한다(예외는 잡 단위로 격리 — 서버 전체로 전파하지 않는다). 토큰은 로그에 남기지
    않는다(``crawl_request`` 전체를 로깅하지 않고 ``job_id`` 만 기록).
    """
    deps.job_store.update(job_id, status=IngestJobStatus.IN_PROGRESS)
    try:
        result = deps.run_crawl(crawl_request)
    except Exception as exc:  # noqa: BLE001 — 크롤/외부 호출 예외 광범위 캐치(잡 단위 격리)
        _LOGGER.exception("ingest job failed: job_id=%s", job_id)
        deps.job_store.update(
            job_id,
            status=IngestJobStatus.FAILED,
            finished_at=datetime.now(UTC),
            error=str(exc),
        )
        return
    failed = len(result.failed_page_ids)
    deps.job_store.update(
        job_id,
        status=IngestJobStatus.COMPLETED,
        total_pages=result.pages_collected + failed,
        processed_pages=result.pages_collected,
        failed_pages=failed,
        finished_at=datetime.now(UTC),
    )


@router.post("/ml/ingest")
async def ingest_route(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    deps: IngestDepsDep,
) -> dict[str, Any]:
    """수집 트리거 (api-spec v2.2.0 §2-2).

    잡을 ``STARTED`` 로 생성하고 백그라운드 태스크로 crawl→chunk→upsert 를 실행한 뒤,
    즉시 ``jobId`` / ``status`` / ``startedAt``(KST)을 반환한다. 진행 상태는
    ``GET /ml/ingest/status/{jobId}`` 로 조회한다.
    """
    job = deps.job_store.create()
    # mode 는 검증만 하고(2단계 PoC) full-crawl 합성 파이프라인으로 처리한다. delta sync
    # 에이전트(data_sync_agent) 배선은 후속 — 토큰은 CrawlRequest 로만 전달하고 로깅하지 않는다.
    crawl_request = CrawlRequest(
        space_key=payload.space_key,
        access_token=payload.access_token,
        cloud_id=payload.cloud_id,
    )
    background_tasks.add_task(_run_ingest_job, deps, job.job_id, crawl_request)
    return {
        "jobId": job.job_id,
        "status": job.status.value,
        "startedAt": _to_kst(job.started_at),
    }


@router.get("/ml/ingest/status/{job_id}")
async def ingest_status_route(job_id: str, deps: IngestDepsDep) -> Any:
    """수집 상태 조회 (api-spec v2.2.0 §2-3).

    잡을 찾으면 ``jobId`` / ``status`` / ``totalPages`` / ``processedPages`` /
    ``failedPages`` / ``startedAt``(KST)를 반환한다. 없으면 4필드 에러 봉투로 404 응답.
    """
    record = deps.job_store.get(job_id)
    if record is None:
        return JSONResponse(
            status_code=404,
            content={
                "isSuccess": False,
                "code": 404,
                "errorCode": "RESOURCE_NOT_FOUND",
                "message": f"수집 작업을 찾을 수 없습니다: {job_id}",
            },
        )
    return {
        "jobId": record.job_id,
        "status": record.status.value,
        "totalPages": record.total_pages,
        "processedPages": record.processed_pages,
        "failedPages": record.failed_pages,
        "startedAt": _to_kst(record.started_at),
    }


@router.get("/ml/ingest/health")
async def ingest_health() -> dict[str, str]:
    """Data Ingestion Pipeline 헬스체크 (api-spec v2.2.0 §2-4-2).

    BFF 가 수집 서버(Confluence 수집/청킹/임베딩)가 정상 응답 가능한지만 확인하는 용도.
    내부 의존성(Vector DB / Confluence / RabbitMQ 등) 상세 상태는 보고하지 않고, 서버가
    요청을 받아 응답할 수 있는 상태인지만 ``{"status": "UP"}`` 로 알린다(§2-4 공통 규칙).
    """
    return {"status": "UP"}
