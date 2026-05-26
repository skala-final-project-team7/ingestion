# Working Log — Data Ingestion Pipeline

작업 중 내린 중요한 결정·변경 이유·부작용 가능성을 시간순으로 기록한다(루트 CLAUDE.md
"작업 중 중요한 결정은 문서에 남긴다"). 상세 Plan 은 `docs/ai/current-plan.md` 참조.

---

## 2026-05-26 — featureI-6: 외부 에이전트 2종 vendoring 통합 (FR-001 / FR-005)

**작업**: 별도 레포에서 개발된 Data Ingestion Agent·Data Sync Agent 두 패키지를 ingestion
파이프라인에 vendoring + 얇은 어댑터로 통합. featureI-2(Full Crawl)·featureI-5(Delta Sync)를
신규 작성이 아닌 외부 에이전트 통합으로 구현.

**결정**

- **vendoring 레이아웃(rag 미러)**: import 가능한 패키지를 저장소 루트로 이동
  (`data_ingestion_agent/`, `data_sync_agent/` — 코드 무수정, 위치만 이동). 에이전트의
  `scripts/`+`tests/` 는 `tests/<agent>/` 아래에 무수정 복사. 에이전트 integration test 가
  `Path(__file__).parents[N]` 로 `scripts/`·`fixtures/` 상대 경로를 참조하므로 원본
  프로젝트 구조(scripts/ + tests/)를 그대로 보존해야 통과한다. 두 에이전트의 동일 테스트
  파일명(test_schema_config.py 등) 충돌을 막기 위해 vendored 테스트 디렉토리에 pytest
  패키지 마커 `__init__.py` 만 추가(브리프 허용 범위).
- **어댑터 구동 = 상단 workflow 블랙박스 호출**: `run_full_crawl_workflow` /
  `run_data_sync_workflow` 를 in-process 로 호출하고 산출물(`.documents`/`.changed_documents`)
  을 메모리에서 `PageObject` 로 변환. 에이전트가 로컬 파일로 쓰는 산출물은 임시 디렉토리로
  우회 후 즉시 정리(파이프라인은 MongoDB `raw_pages` 적재 + Chunking Queue 발행).
- **pyproject**: `packages.find.include` 에 `data_ingestion_agent*`/`data_sync_agent*` 추가,
  `[tool.ruff] extend-exclude` + `[tool.mypy] exclude` 에 vendored 패키지·테스트 경로 추가
  (원본을 우리 컨벤션 line-length 100 등에 맞추지 않고 무수정 보존). langgraph 는 선택
  의존성(`agents` extra) — 미설치 시 두 에이전트 모두 sequential fallback.

**계획 대비 실측 차이(에이전트 MVP)**

- 에이전트는 자체 `ProcessedDocument`(중첩 space/page/body/metadata) 스키마를 산출 →
  어댑터가 평면 `PageObject` 로 변환(`body_html←body.storage_html`,
  `last_modified←page.last_modified_at`, `webui_link←page.page_url` 등).
- ACL / labels / ancestors / 첨부(not_supported_in_mvp) 미산출 → ACL 은 space_key 합성
  (`synthesize_space_acl`, JsonFixture PoC 패턴 동일), labels/ancestors/attachments 는 빈 값.
- `IngestionStage` enum 에 crawl/ingest 단계가 없어(공유 자산 — RAG `ingestion_jobs` 대시보드와
  계약 공유) crawl 단계 `ingestion_jobs` 기록은 본 change-set 에서 보류. `CrawlResult`/
  `DeltaSyncResult` 를 잡 리포트로 사용. enum 추가는 RAG 분기 영향 설명 후 별도 협의.

**미해결(추측 구현 금지 — current-plan.md featureI-6 TBD)**

- ACL 실연동(space_key vs content restrictions) — RAG 검색 ACL 필터와 공유 계약.
- `access_token`/`cloud_id` 전달 경로(Auth Server→BFF→Ingestion) — PoC placeholder(Settings
  env 주입 / CrawlRequest·DeltaSyncRequest 주입) 유지.
- 첨부 수집·추출(FR-002, featureI-3), Trash API/Webhook 삭제(에이전트·본 레포 모두 MVP 제외),
  삭제 후보 Qdrant soft_delete 실행(store 소유 Worker 책임), snapshot Mongo 영속화.

**검증**

- 샌드박스(Python 3.10)는 vendored 의 `enum.StrEnum`(3.11+) import 불가 + 의존성 미설치 →
  여기서는 ruff(app·tests 통과) + `py_compile`(vendored 포함 전체 syntax OK)까지만 수행.
- 전체 pytest·`./scripts/verify.sh`·git push 는 Mac(3.11)에서 수행 필요.

**보안**: access_token 은 Settings `SecretStr`/주입 인자로만 다루고 로그·메시지 페이로드·
테스트 픽스처에 남기지 않음(에이전트 자체 redaction + 어댑터 placeholder).
