"""Atlassian Document Source Adapter — vendored Data Ingestion Agent 연결 [Agent 경계].

--------------------------------------------------
작성자 : 최태성
작성목적 : 저장소 루트에 무수정 vendoring 된 Data Ingestion Agent(FR-001 Confluence Full
          Crawl)를 ``DocumentSourceAdapter`` 계약으로 감싼다. 에이전트는 자체
          ``ProcessedDocument`` 스키마(space/page/body/metadata 중첩)를 산출하므로, 본
          어댑터가 이를 ingestion 표준 ``PageObject`` 로 변환한다(vendored 무수정 보존,
          모든 변환은 어댑터에서 수행). 파이프라인 본체(crawler/sync)는 어떤 공급원인지
          알지 못한 채 표준 PageObject 스트림만 소비한다.
작성일 : 2026-05-26
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-26, 최초 작성, featureI-6 — vendored Data Ingestion Agent in-process
    호출(run_full_crawl_workflow 블랙박스) + ProcessedDocument→PageObject 매핑 +
    space_key 기반 PoC ACL 합성.
--------------------------------------------------
[호환성]
  - Python 3.11.x (vendored 에이전트가 enum.StrEnum 사용)
  - vendored ``data_ingestion_agent`` 패키지(저장소 루트) 필요
--------------------------------------------------
[미해결 사항(추측 구현 금지 — docs/atlassian-api.md / docs/ai/current-plan.md)]
  - ACL: 에이전트 MVP 는 ACL 을 산출하지 않는다(content restrictions API 미사용).
    PoC 는 ``_synthesize_acl`` 로 space_key 기반 합성. 실연동은 팀(+RAG ACL 필터) 결정 대기.
  - labels / ancestors / attachments: 에이전트 MVP 미산출(첨부는 not_supported_in_mvp).
    어댑터는 빈 값으로 매핑하고, 채워지는 시점은 후속 feature 로 남긴다.
  - access_token / cloud_id 전달 경로(Auth Server→BFF→Ingestion): 미확정. 호출자가
    ``CrawlRequest`` 또는 Settings placeholder 로 주입한다. 로그·메시지에 남기지 않는다.
--------------------------------------------------
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from app.adapters.base import ActiveIds, ChangeEvent, DocumentSourceAdapter
from app.adapters.json_fixture import parse_atlassian_datetime
from app.schemas.page_object import PageObject

if TYPE_CHECKING:
    from app.config import Settings


class _WorkflowRunner(Protocol):
    """vendored full crawl workflow 호출 시그니처 — 테스트 주입 지점."""

    def __call__(self, *, config: Any, client: Any | None = None) -> Any:
        """Full crawl workflow 를 실행하고 ``.documents`` 를 가진 결과를 반환한다."""


class AtlassianSourceAdapter(DocumentSourceAdapter):
    """vendored Data Ingestion Agent 를 ``DocumentSourceAdapter`` 로 감싼 어댑터.

    Full Crawl 은 vendored ``run_full_crawl_workflow`` 를 in-process 로 호출(블랙박스)하고,
    산출 ``ProcessedDocument`` 목록을 표준 ``PageObject`` 로 변환한다. 에이전트는 로컬
    파일로 산출물을 쓰므로 임시 디렉토리로 출력을 우회하고(파이프라인은 MongoDB
    ``raw_pages`` 에 적재), 메모리 결과(``result.documents``)만 소비한다.

    Args:
        cloud_id: Atlassian Cloud ID(외부 주입). 빈 값이면 실행 시 에이전트가 검증 실패.
        access_token: Confluence access token(외부 주입). 로그·메시지에 남기지 않는다.
        client: vendored 에이전트의 Confluence client. None 이면 에이전트가 운영용
            ``ConfluenceClient`` 를 생성한다. 테스트는 fake client 를 주입한다.
        workflow_runner: full crawl workflow 호출자. 기본값은 vendored
            ``run_full_crawl_workflow``. 테스트에서 교체 가능.
        request_delay_seconds / max_retries / timeout_seconds: 에이전트 호출 속도·재시도 설정.
    """

    def __init__(
        self,
        *,
        cloud_id: str,
        access_token: str,
        client: Any | None = None,
        workflow_runner: _WorkflowRunner | None = None,
        request_delay_seconds: float = 0.3,
        max_retries: int = 3,
        timeout_seconds: int = 20,
    ) -> None:
        self._cloud_id = cloud_id
        self._access_token = access_token
        self._client = client
        self._workflow_runner = workflow_runner
        self._request_delay_seconds = request_delay_seconds
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Settings) -> AtlassianSourceAdapter:
        """Settings 의 placeholder 자격증명으로 어댑터를 생성한다(팩토리 경로).

        access_token/cloud_id 전달 경로가 확정되기 전 PoC placeholder 다. 자격증명이
        비어 있으면 실행 시 vendored 에이전트의 config 검증에서 실패한다.
        """
        return cls(
            cloud_id=settings.atlassian_cloud_id,
            access_token=settings.atlassian_access_token.get_secret_value(),
            request_delay_seconds=settings.atlassian_request_delay_seconds,
            max_retries=settings.atlassian_max_retries,
            timeout_seconds=settings.atlassian_timeout_seconds,
        )

    # --- DocumentSourceAdapter 인터페이스 ---

    def fetch_pages(self, since: datetime | None = None) -> Iterator[PageObject]:
        """vendored Full Crawl 을 실행하고 표준 PageObject 스트림으로 변환해 반환한다.

        Args:
            since: 지정 시 ``last_modified`` 가 since 이후인 페이지만 반환(증분).
                None 이면 전체(Full Crawl). 에이전트 MVP 는 항상 전체를 수집하므로
                증분 필터는 어댑터에서 ``last_modified`` 비교로 적용한다.
        """
        for document in self._collect_documents():
            page = self._to_page_object(document)
            if since is not None and page.last_modified < since:
                continue
            yield page

    def list_active_ids(self) -> ActiveIds:
        """공급원에 현재 살아있는 페이지 ID 집합(Reconciliation 대조용).

        에이전트 MVP 는 첨부를 수집하지 않으므로 ``attachments`` 는 빈 집합이다.
        """
        ids = ActiveIds()
        for document in self._collect_documents():
            ids.pages.add(document.page.page_id)
        return ids

    def watch_changes(self) -> Iterator[ChangeEvent]:
        """실시간 변경 이벤트 — 에이전트 MVP 는 Webhook 미지원이라 빈 스트림."""
        yield from ()

    # --- 내부 헬퍼 ---

    def _collect_documents(self) -> list[Any]:
        """vendored full crawl workflow 를 1회 실행해 ProcessedDocument 목록을 반환한다.

        에이전트는 산출물을 로컬 파일로 쓰므로 임시 디렉토리로 우회하고(즉시 정리),
        메모리 결과만 사용한다. 파이프라인 적재(raw_pages)는 crawler 가 담당한다.
        """
        runner = self._workflow_runner or _default_workflow_runner()
        config = self._build_config(output_dir=tempfile.mkdtemp(prefix="ingestion-agent-"))
        try:
            result = runner(config=config, client=self._client)
            return list(result.documents)
        finally:
            shutil.rmtree(str(config.output_dir), ignore_errors=True)

    def _build_config(self, *, output_dir: str) -> Any:
        from data_ingestion_agent.config import DataIngestionConfig

        return DataIngestionConfig(
            cloud_id=self._cloud_id,
            access_token=self._access_token,
            output_dir=output_dir,
            request_delay_seconds=self._request_delay_seconds,
            max_retries=self._max_retries,
            timeout_seconds=self._timeout_seconds,
        )

    def _to_page_object(self, document: Any) -> PageObject:
        """vendored ProcessedDocument → 표준 PageObject 변환(모든 변환은 어댑터에서).

        매핑(docs/atlassian-api.md 매핑표 + 에이전트 ProcessedDocument 스키마):
            page.page_id            → page_id
            space.space_key         → space_key
            page.title              → title
            body.storage_html       → body_html  (청커가 HTML 파싱)
            page.version_number     → version_number
            page.last_modified_at   → last_modified (ISO 8601 파싱)
            page.page_url           → webui_link
            (없음)                  → allowed_groups/allowed_users (PoC 합성)
            (MVP 미산출)            → labels=[] / ancestors=[] / attachments=[]
        """
        space_key = document.space.space_key
        allowed_groups, allowed_users = self._synthesize_acl(space_key)
        return PageObject(
            page_id=document.page.page_id,
            space_key=space_key,
            title=document.page.title,
            body_html=document.body.storage_html,
            version_number=document.page.version_number,
            last_modified=_parse_last_modified(document.page.last_modified_at),
            allowed_groups=allowed_groups,
            allowed_users=allowed_users,
            webui_link=document.page.page_url,
            labels=[],
            ancestors=[],
            attachments=[],
        )

    def _synthesize_acl(self, space_key: str) -> tuple[list[str], list[str]]:
        """PoC ACL 합성 — space_key 기반 그룹(JsonFixtureSourceAdapter 패턴 동일).

        실 ACL 연동(content restrictions vs space 접근권한) 결정 시 본 메서드만 교체한다
        (docs/db-schema.md §1.4 미해결 사항 — RAG 검색 ACL 필터와 공유 계약).
        """
        return synthesize_space_acl(space_key)


def synthesize_space_acl(space_key: str) -> tuple[list[str], list[str]]:
    """PoC ACL 합성(space_key 기반) — Full Crawl·Delta Sync 어댑터가 공유한다.

    에이전트 MVP 가 ACL 을 산출하지 않으므로 ``["space:{space_key}"]`` 그룹으로 합성한다.
    실 ACL 연동 결정 시 이 함수만 교체한다(RAG 검색 ACL 필터와 공유 계약).
    """
    return [f"space:{space_key}"], []


def _default_workflow_runner() -> _WorkflowRunner:
    """기본 workflow runner — vendored full crawl workflow 를 지연 import 한다.

    지연 import 로 vendored(StrEnum, Python 3.11) 의존을 import 시점이 아닌 실행 시점으로
    미룬다(app 패키지가 vendored 미설치 환경에서도 import 가능하도록).
    """
    from data_ingestion_agent.workflow import run_full_crawl_workflow

    runner: _WorkflowRunner = run_full_crawl_workflow
    return runner


def _parse_last_modified(value: str) -> datetime:
    """에이전트 ``last_modified_at``(ISO 8601) → datetime.

    빈 문자열은 매핑 실패로 간주해 명시적으로 거부한다(epoch 등으로 무음 보정하면 Delta
    Sync 비교가 오염되므로). 정상 에이전트 산출물은 version.createdAt 으로 항상 채워진다.
    """
    if not value:
        raise ValueError("last_modified_at is required for PageObject mapping")
    return parse_atlassian_datetime(value)
