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

---

## 2026-05-26 — featureI-4: Chunking+Embedding Worker 배선 (FR-003 / FR-004)

**작업**: featureI-6로 연결된 앞 절반(crawl→raw_pages→`content.chunking` 발행)을 이어받아,
`content.chunking` 메시지를 소비해 Adaptive Chunker → Dual Embedding → Qdrant upsert 까지
배선. 끊겨 있던 큐 소비 단계를 채워 수집 파이프라인을 end-to-end로 연결.

**결정**

- **단일 Worker 토폴로지(A)**: 복사된 `indexer.index_chunks` 가 embed+upsert+cache 를 결합하므로
  하나의 Worker 가 `content.chunking` 소비 → `raw_pages.get_page` → `chunk_page` → `index_chunks`
  를 수행한다. `content.embedding` 큐는 상수로 예약(운영 스케일링 시 2-Worker 분리 여지).
- **doc_type 폴백 우선**: `chunk_page(page)` 의 라벨 휴리스틱(`infer_doc_type`, 미매칭 시
  operation) 사용. GPT-4o-mini 문서 분석기[Agent] + MySQL `space_doc_type_cache` 는 featureI-4b 후속.
- **의존성 주입**: 임베더/Qdrant/cache/raw_store/jobs 를 `ChunkingWorkerDeps` 로 주입(테스트는 Fake).
  실 어댑터(E5/BM25/Qdrant from_settings) 부트스트랩은 배포 wiring(후속).
- **잡 기록**: 단일 Worker 라 색인 종단 단계인 `IngestionStage.UPSERT` 로 1건 기록(SUCCESS /
  INVALID_ACL / EMPTY_BODY — 모두 기존 enum 값). crawl 단계와 달리 stage enum 이 존재해 기록 가능.

**게이트(app/CLAUDE.md §3 정합)**: ACL 누락 페이지(`is_acl_missing`)는 색인하지 않고 INVALID_ACL,
청크 0건은 EMPTY_BODY, `page_id` 가 raw_pages 에 없으면 `RawPageNotFoundError`(상위 DLQ).

**신규/수정 파일**: `app/ingestion/workers/consumer.py`(MessageConsumer ABC+Fake+Pika),
`app/ingestion/workers/chunking_worker.py`(process_chunking_message + run_chunking_worker),
`app/storage/raw_store.py`(`get_page` 읽기 추가 — Fake/Mongo), `tests/ingestion/test_chunking_worker.py`,
`docs/architecture.md`·`docs/ai/current-plan.md`.

**검증**: ruff check/format + mypy app(41 files) 통과(샌드박스). 멱등성(동일 version 재실행 skip)·
ACL/빈 본문 게이트·잡 기록·end-to-end 를 Fake(임베더/Qdrant/cache)로 테스트. 전체 pytest·
`./scripts/verify.sh`·push 는 Mac(3.11)에서.

**후속(TBD)**: featureI-4b(GPT-4o-mini 문서 분석기 + space_doc_type_cache), 첨부 청크 경로
(FR-002 첨부 입력 생성 후), 실 어댑터 부트스트랩 + pika consumer 배포 wiring, `content.embedding`
2-Worker 분리(운영 스케일링 시).

---

## 2026-05-26 — featureI-4b: 문서 분석기 [Agent] + MySQL space_doc_type_cache (FR-003)

**작업**: featureI-4 의 라벨 휴리스틱 폴백을 대체해, 스페이스 단위 1회 GPT-4o-mini doc_type
판별 → MySQL `space_doc_type_cache` 캐싱 → 이후 같은 스페이스 페이지는 캐시 재사용. Chunking
Worker 에 optional resolver 로 연결.

**결정**

- **LLM 격리(Agent)**: `DocTypeClassifier` ABC + `FakeDocTypeClassifier` + `OpenAIDocTypeClassifier`
  (GPT-4o-mini, **Function Calling 으로 스키마 강제**, 타임아웃). 비결정론 LLM 을 어댑터 경계에
  격리해 테스트는 Fake 로 대체(app/CLAUDE.md §5).
- **폴백**: 신뢰도 < 0.6 또는 LLM 실패 시 `DocType.OPERATION`. DocType enum 에 'general' 이 없어
  (chunker 폴백·db-schema confidence 주석과 정합) operation 사용 — CLAUDE.md §FR-003 의 'general'
  표기와의 차이는 본 로그에 명시. **저신뢰는 캐싱**(반복 호출 방지), **일시적 LLM 실패는 미캐싱**
  (다음 페이지 재시도).
- **스페이스 1회 판별**: 캐시 우선. 미스 시 현재 페이지 1샘플(title+labels+body 일부)로 분류 후
  캐싱(sample_count=1). 다중 샘플 스페이스 분석은 TBD.
- **Worker 연동(비파괴)**: `ChunkingWorkerDeps.doc_type_resolver`(optional) 추가. 주입 시
  `chunk_page(page, resolver.resolve_doc_type(page))`, 미주입 시 기존 라벨 폴백 — featureI-4 동작 무변.

**신규/수정 파일**: `app/storage/space_doc_type_cache.py`(ABC+Fake+MySQL, db-schema §3.1),
`app/ingestion/document_analyzer.py`(분석기[Agent]), `app/ingestion/workers/chunking_worker.py`
(resolver 연동), `app/storage/__init__.py`(export), `tests/ingestion/test_document_analyzer.py`,
`docs/architecture.md`·`docs/ai/current-plan.md`.

**검증**: ruff check/format + mypy app(43 files) 통과. 캐시 미스→분류→캐싱, 캐시 히트 재사용(LLM
재호출 없음), 저신뢰/예외→OPERATION 폴백, Worker 가 resolver doc_type(incident)으로 청킹을 Fake 로
테스트. 전체 pytest·verify.sh·push 는 Mac(3.11).

**후속(TBD)**: 다중 샘플 스페이스 분석, 실 OpenAI/MySQL 부트스트랩 + Worker 에 resolver 주입 배포
wiring, 첨부 분석기(attachment_analyzer) 연동(FR-002 이후).

---

## 2026-05-26 — featureI-3: 첨부 텍스트 추출기 코어 (FR-002)

**작업**: 첨부 바이너리(PDF/Word/Excel/CSV) → 텍스트 추출 결정론 Pipeline 구현. 이미지·도형 제외,
Excel/CSV 는 시트→자연어 직렬화. self-contained(공급원 무관, bytes→text).

**스코프 결정**: vendored 에이전트 MVP 가 첨부를 수집하지 않아(`not_supported_in_mvp`)
`raw_attachments` 입력이 없으므로, 이번엔 **추출기 코어 + 단위 테스트**만 구현. 첨부 수집기
(Confluence Attachment API 다운로드 → `raw_attachments`)·`attachment_texts` 적재·Attachment/Chunking
Queue 배선·chunker `chunk_attachment` 연결은 후속(featureI-3b). 수집은 에이전트/어댑터 확장 선행 필요.

**구현**

- `extractor/pdf.py` — PyMuPDF(fitz) 1차 → 예외/빈 결과 시 pdfplumber 폴백. `RAW_TEXT`.
- `extractor/docx.py` — python-docx 문단 + 표(행 `cell | cell`). `RAW_TEXT`.
- `extractor/spreadsheet.py` — openpyxl(xlsx)/csv → `Sheet: <name>` + `헤더: 값` 직렬화. `SHEET_SERIALIZED`.
- `extractor/base.py`(stub→구현) — 유형 디스패치 + **graceful degrade**(예외 → `ok=False` + reason).
  라이브러리는 각 모듈 함수 내 **지연 import**(app import 가 extras 미설치에서도 동작).

**보안**: 실패 reason 에는 **예외 타입명만** 남기고 첨부 내용·자격증명을 포함하지 않는다.

**검증**: ruff/format + mypy app(extras 설치해 Mac 재현, 46 files) 통과. 추출 4유형은 샌드박스
(Python 3.10)에서 모듈 standalone 로드(StrEnum 미경유)로 실제 라이브러리 구동 스모크 통과
(DOCX 문단+표 / XLSX·CSV 직렬화 / PDF 텍스트 / 손상 PDF → graceful degrade). 전체 pytest 는 Mac.
`tests/ingestion/test_attachment_extractor.py`(in-test 파일 생성, 미설치 라이브러리는 importorskip),
`tests/test_scaffold.py` 추출기 stub 테스트를 구현 계약(CSV=stdlib) 검증으로 갱신.

**후속(featureI-3b TBD)**: 첨부 수집기(다운로드→raw_attachments), `attachment_texts` 적재,
Attachment/Chunking Queue(첨부) 배선, chunker `chunk_attachment` 경로 연결.

---

## 2026-05-26 — featureI-7: 파이프라인 조립(composition) + in-process end-to-end PoC

**작업**: featureI-6(crawl→raw_pages→`content.chunking`)·featureI-4(chunking_worker→Qdrant)로
나뉜 두 절반을 in-process 로 합성해 전 체인 end-to-end 동작을 검증. 재사용 가능한
`FakeQdrantPoolStore`(PoC 모드 enabler)도 신설.

**구현**

- `app/storage/qdrant_fake.py` — `FakeQdrantPoolStore`(in-memory). `upsert_chunks_batch`/
  `scroll_page_ids`/`scroll_attachment_ids`/`delete_by_*` 를 `QdrantPoolStore` 와 호환 구현.
  **공유 `qdrant_client.py` 무수정**(additive 새 모듈). 검색(search)은 Query 단계 책임이라 미구현.
- `app/ingestion/pipeline.py` — `run_ingestion_pipeline`(crawl → 발행 `content.chunking` 메시지
  in-process drain → chunking_worker) + `build_poc_components`(all-fakes, **raw_store 를 crawl·worker
  공용 인스턴스로 공유**) + `run_poc_ingestion` 편의 함수. `PipelineResult`/`PocComponents`.

**설계 메모**: 본 합성은 **PoC/테스트용**이다(운영은 crawl·chunking_worker 를 RabbitMQ 로 분리해
독립 스케일링 — featureI-7b 배포 wiring). `run_ingestion_pipeline` 은 발행 메시지를 in-process 로
drain 하므로 `FakeQueuePublisher` 전제. raw_store 공유가 핵심(메시지엔 page_id 만, 본문은 raw_store 로드).

**검증**: ruff/format + mypy app(48 files) 통과. end-to-end 테스트 `tests/ingestion/test_pipeline_e2e.py`
— ① crawl→raw_pages→발행→worker→FakeQdrantPoolStore 적재(scroll_page_ids 검증), ② 동일 raw_store·
cache·store 공유 재실행 멱등성(재upsert 스킵), ③ ACL 누락 페이지 색인 차단 전파. 전체 pytest 는 Mac.

**후속(featureI-7b TBD)**: 실 어댑터(E5/BM25/Qdrant/Mongo from_settings) 부트스트랩 + pika consumer
실행 loop + CLI 엔트리포인트(인프라 의존 — 통합 환경 검증).
