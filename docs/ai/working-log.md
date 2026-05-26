# Working Log — Data Ingestion Pipeline

작업 중 내린 중요한 결정·변경 이유·부작용 가능성을 시간순으로 기록한다(루트 CLAUDE.md
"작업 중 중요한 결정은 문서에 남긴다"). 상세 Plan 은 `docs/ai/current-plan.md` 참조.

---

## 2026-05-26 — ADR 0003 항목 3 운영 wiring: crawl 잡 기록 연결 (ingestion 단독)

**작업**: 항목 3에서 `crawler.run_full_crawl`에 optional `jobs` 주입을 추가했으나, in-process
조립(`pipeline.py`)에서는 crawl 에 jobs 가 연결되지 않아 CRAWL 잡이 실제로 기록되지 않았다.
crawl 과 chunking_worker 가 **동일 jobs 인스턴스를 공유**하도록 연결해 한 `ingestion_jobs` 에
CRAWL(페이지별) + UPSERT 가 함께 남도록 했다.

**변경(ingestion 단독 — 공유 자산·rag 무변경)**

- `app/ingestion/pipeline.py`:
  - `run_ingestion_pipeline` 이 `run_full_crawl(..., jobs=chunking_deps.jobs)` 로 호출 — crawl 과
    worker 가 같은 jobs 인스턴스 공유(비파괴: jobs None 이면 양쪽 모두 기록 생략).
  - `build_poc_components` 가 `FakeIngestionJobsRepository` 를 생성해 `ChunkingWorkerDeps.jobs` 에
    주입하고 `PocComponents.jobs` 로 노출(이전엔 jobs 미주입 → None 이라 PoC 에서 기록 안 됨).
- `tests/ingestion/test_pipeline_e2e.py`: PoC 전 체인 실행 후 공유 jobs 에 CRAWL(페이지별 SUCCESS)
  + UPSERT 가 함께 기록되는지 검증하는 테스트 추가.

**범위 메모**: 실 운영 경로(`bootstrap.build_chunking_worker_deps` 의 Mongo jobs)는 RabbitMQ consumer
실행 loop(featureI-7c, 인프라 의존 TBD)에서 동일하게 `run_full_crawl(jobs=deps.jobs)` 로 연결하면
된다 — 본 change-set 은 in-process 조립(PoC/테스트) wiring 까지다.

**검증**: ruff(line-length 100, 통과) + py_compile(통과). 전체 `./scripts/verify.sh` 는 Mac(3.11).
공유 자산 무변경이라 rag 영향 없음(ingestion 단독 커밋).

---

## 2026-05-26 — ADR 0003 항목 4 적용: soft_delete 도입 (승인됨)

**작업**: ADR 0003 항목 4(승인 필요로 보류했던 항목)를 사용자 승인 후 적용. Qdrant payload에
soft-delete 플래그를 도입하고, rag 검색이 삭제분을 제외하도록 공유 계약을 확장.

**변경(공유 자산 — 양 레포 동시·바이트 동일)**

- `app/ingestion/vector_store.py:build_point_payload`: `"is_deleted": False` 추가(신규/재색인
  upsert 기본값). owning source rag 먼저 → ingestion 미러.
- `app/storage/qdrant_client.py`:
  - `_BOOL_INDEX_FIELDS=("is_deleted",)` + `_ensure_payload_indexes`에 BOOL 인덱스 생성.
  - `_build_combined_filter`에 `must_not(is_deleted=true)` 추가 — 모든 검색이 삭제분 제외.
    필드 부재(legacy)는 매칭 안 돼 자연 통과(미삭제 간주, 재색인 없이 후방 호환).
  - `soft_delete_by_page_id`/`soft_delete_by_attachment_id`/`_soft_delete_by_field`(set_payload)
    추가 — Point 보존하고 `is_deleted`만 True. hard delete(`delete_by_*`)는 그대로 보존.
  - rag에서 편집 후 ingestion에 파일 단위 복사로 바이트 동일 보장.

**변경(레포 전용)**

- (ingestion) `app/storage/qdrant_fake.py`: `_StoredPoint.is_deleted` 추가 + 실 store와 동일한
  `soft_delete_by_*`(dataclasses.replace) — 드롭인 인터페이스 정합.
- (ingestion) `tests/ingestion/test_qdrant_fake.py`(신규): Fake soft_delete 가 플래그만 갱신하고
  Point 보존하는지 검증.
- (rag) `tests/storage/test_qdrant_client.py`: soft_delete 가 검색 제외 + Point 보존(count 불변),
  첨부 soft_delete, 미삭제 청크 정상 검색 테스트 추가.
- (rag) `tests/ingestion/test_vector_store.py`: payload `is_deleted` 기본 False 단언.
- 양 레포 `docs/db-schema.md` §1.2(is_deleted 행)·§1.3(bool 인덱스), `docs/adr/0003` 항목 4 상태
  "적용됨".

**영향/주의**: soft/hard delete는 호출 측 선택. **양 레포 동시 배포** 필요. 기존 인덱스의 Point는
`is_deleted` 필드가 없어 자동으로 "미삭제"로 동작하지만, 명시적으로 채우려면 재색인 또는 일괄
`set_payload` 백필이 필요하다. 삭제 트리거(Delta Sync `deleted_candidate`/Trash/Webhook →
`soft_delete_by_*`) 실배선은 store를 소유한 Sync Worker의 운영 wiring 후속(능력은 도입 완료).

**검증**: 샌드박스(3.10)는 qdrant-client/StrEnum 부재로 pytest 불가 → ruff(line-length 100, 통과) +
py_compile(통과) + 공유 자산 바이트 동일 확인. **전체 `./scripts/verify.sh`는 양 레포 Mac(3.11)에서
수행 필요**(특히 rag `:memory:` Qdrant 통합 테스트 — soft_delete 검색 제외).

---

## 2026-05-26 — ADR 0003 항목 3 적용: IngestionStage.CRAWL 추가 + crawl 잡 기록 (승인됨)

**작업**: ADR 0003 항목 3(승인 필요로 보류했던 항목)을 사용자 승인 후 적용. 공유 enum
`IngestionStage`에 수집 단계 값을 추가하고 ingestion crawl 단계 `ingestion_jobs` 기록을 배선.

**변경(공유 자산 — 양 레포 동시·바이트 동일)**

- `app/schemas/enums.py`: `IngestionStage`에 `CRAWL = "crawl"` 추가(파이프라인 순서상 ANALYZE 앞).
  owning source인 rag를 먼저 수정하고 ingestion에 동일 미러 — `diff`로 바이트 동일 확인.

**변경(레포 전용)**

- (ingestion) `app/ingestion/crawler.py`: `run_full_crawl`에 `jobs: IngestionJobsRepository | None
  = None` 추가. 주입 시 적재·발행에 성공한 페이지마다 `IngestionStage.CRAWL` + `IngestionStatus.
  SUCCESS` 기록(비파괴 — 미주입이면 기존 동작). 실패 페이지는 적합한 status 코드가 없어
  `failed_page_ids`로만 격리(잡 레코드 미기록). `datetime.now(UTC)` 사용(기존 chunking_worker 정합).
- (ingestion) `tests/ingestion/test_crawler.py`: jobs 주입 시 CRAWL SUCCESS 기록 / 실패 페이지 미기록
  테스트 2건 추가.
- (rag) `tests/schemas/test_enums.py`: `IngestionStage` 멤버셋에 `crawl` 추가(기존 동치 assert 갱신).
- 양 레포 `docs/db-schema.md` §2.3 `stage` 설명에 `crawl` 반영, `docs/adr/0003` 항목 3 상태를
  "적용됨"으로 갱신.

**영향/주의**: 공유 enum 변경이므로 **양 레포를 함께 배포**해야 한다. ingestion이 `"crawl"`을 기록한
`ingestion_jobs` 레코드를, enum 미갱신 레포(또는 대시보드)가 `IngestionStage(value)`로 역파싱하면
`ValueError`가 날 수 있다(ADR 0003 항목 3 영향). 관리자 대시보드는 별도 시스템 — stage 화이트리스트
확인 권장.

**검증**: 샌드박스(Python 3.10)는 StrEnum/venv 부재로 pytest 불가 → ruff(line-length 100, 통과) +
py_compile(통과)까지 수행. **전체 `./scripts/verify.sh`(format→lint→test)는 양 레포 Mac(3.11)에서
수행 필요**(특히 rag test_enums, ingestion test_crawler).

**후속**: ADR 0003 항목 4(soft_delete)는 별도 change-set로 이어서 진행. crawl 잡 기록의 실 배선
(bootstrap에서 `jobs` 주입)은 운영 wiring 시 연결.

---

## 2026-05-26 — ingestion↔rag 공유 계약 합의 (ADR 0003)

**작업**: ingestion·rag 두 레포가 공유하는 미해결 계약(TBD)을 식별·결정하고 ADR로 동결. 결정 결과를
양 레포 `docs/adr/0003`에 **동일 복제**하고 관련 문서를 정합 갱신. 코드 diff로 공유 자산 분기 현황을
직접 검증(추측 금지).

**검증한 현황(diff/grep)**

- 공유 자산(`app/schemas/*`, `vector_store`, `indexer`, `embedding`, `qdrant_client`, `jobs`,
  `mongo_cache`, `adapters/{base,json_fixture}`)은 rag와 **바이트 동일**. 유일 분기 = `sync.py`
  (본 레포가 `run_delta_sync` additive 추가, 공유 `reconcile_deletions`는 동일).
- `chunk_id=SHA1("{page_id}:{chunk_index}:{attachment_id}")`, cache 키=`(chunk_id, version_number)`,
  payload=`build_point_payload` — 양 레포 동일. rag 검색에 soft-delete 필터 없음(grep 확인).

**결정(상세는 ADR 0003)**

- **항목 1 ACL: (A) `space_key` 합성 확정**(ADR 0002 `space:` prefix 전제). seam =
  `synthesize_space_acl`/`_synthesize_acl`(본 레포) ↔ rag `build_acl_filter`. 런타임 무변.
- **항목 2 payload/cache/chunk_id: owning=rag**, 변경 시 양 레포 동시 + 재색인. 분기 등록부 기록.
- **항목 3 `IngestionStage`에 `CRAWL` 추가: 제안만 — 승인 필요**(공유 enum, 동시 배포 필요). crawl 잡
  기록 보류 현행 유지. enum 코드 미변경.
- **항목 4 soft_delete: PoC는 hard delete 유지**. 도입 규약(payload `is_deleted`+검색 `must_not`+재색인)
  만 기록 — **승인 필요**. `sync.py`의 `deleted_candidate` surface(미파괴)는 현행 유지.
- **항목 5 공유 자산: 복사 유지**, 분리는 분기 비용 증가 시 재검토.
- **합의 불필요**: `access_token`/`cloudId` 전달(Auth/BFF — 두 레포에 코드 없음), JWT 발급·서명,
  관리자 대시보드 데이터.

**수정 파일**: `docs/adr/0003-ingestion-rag-shared-contracts.md`(신규), `docs/db-schema.md`(§1.4 ACL·
§2.3 stage 노트), `docs/atlassian-api.md`(ACL 절 "미해결"→"결정"), 본 `working-log.md`.
**런타임 코드·공유 자산(schemas/enums/vector_store 등) 미변경** — 문서/거버넌스 정합만.

**검증**: 문서 전용 변경이라 코드/테스트 무영향. `git diff`로 `docs/` 한정 확인. 비밀정보 미포함.

**후속(승인 대기)**: 항목 3(enum `CRAWL`)·항목 4(soft_delete)는 사람 승인 후 별도 change-set로
양 레포 동시 적용. 영향·절차는 ADR 0003 "사람 승인이 필요한 항목 요약" 표 참조.

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

---

## 2026-05-26 — featureI-7b: 의존성 부트스트랩(composition root) (FR-003/FR-004 조립)

**작업**: Worker·crawl 이 쓰는 외부 의존성을 `Settings.use_real_adapters` 토글로 PoC(전부 Fake) 또는
실 어댑터로 조립하는 composition root. config.py 의 use_real_adapters 패턴 재사용.

**구현**: `app/ingestion/bootstrap.py`
- `build_raw_page_store(settings)` — PoC `FakeRawPageStore` / 실 `MongoRawPageStore.from_settings`.
- `build_document_analyzer(settings)` — PoC `None`(chunk_page 라벨 폴백) / 실 `DocumentAnalyzer`
  (`OpenAIDocTypeClassifier` + `MySQLSpaceDocTypeCache`).
- `build_chunking_worker_deps(settings, *, raw_store=None)` — PoC Fake 전부(FakeQdrantPoolStore 등) /
  실 E5+BM25+Qdrant.from_settings+Mongo cache/jobs+분석기. `raw_store` 주입 시 crawl·worker 공유
  (in-process PoC). 실 어댑터는 **함수 내 지연 import**(torch/qdrant/openai 를 실행 시점으로 미룸).

**검증**: ruff/format + mypy app(49 files) 통과. PoC 모드 빌더(Fake 반환·raw_store 공유) 단위 테스트
`tests/ingestion/test_bootstrap.py`. 실 어댑터 모드는 인프라 의존이라 통합 환경에서 검증.

**후속(featureI-7c TBD)**: pika consumer/publisher 실행 loop + CLI 엔트리포인트(RabbitMQ 연결).
`Settings` 에 `rabbitmq_url` 추가 필요.

---

## 2026-05-26 — featureI-3b: 첨부 청킹 체인 배선 (FR-002 → 청킹 경로 연결)

**작업**: featureI-3(추출기 코어)에서 보류했던 첨부 **청킹 체인**을 배선했다. 추출기 코어는
이미 있었고, 비어 있던 것은 첨부가 청크가 되어 Qdrant 에 적재되는 경로였다.

**설계 결정(사용자 승인)**: 첨부 청킹은 **rag 레포 ingestion 그래프와 동일하게 파일 기반
`chunk_attachment`** 로 처리한다(파일을 직접 읽어 청크 생성). 별도 `attachment_texts` 컬렉션은
청킹 경로에 두지 않고, `extracted_text` 는 `raw_attachments` 에 함께 보존한다
(`analyze_attachment` 의 길이·반복비율 유효성 게이트 입력으로만 사용). 작업 범위는 **Fake 로
검증 가능한 전부**이며, 실 Confluence 다운로드 어댑터와 pika 실행 loop 는 인프라 의존 후속.

**구현**

- `app/storage/raw_store.py` — `get_attachment(attachment_id)` 읽기 메서드(ABC/Fake/Mongo).
  본문(`get_page`)과 대칭. Mongo 는 `projection={"_id": 0}` + `Attachment.model_validate`.
- `app/ingestion/workers/chunking_worker.py` — `process_chunking_message` 가 `source_type`
  으로 본문/첨부 분기(기본 `page`, 회귀 무영향). `_process_attachment_message`: raw_pages/
  raw_attachments 로드 → **부모 페이지 ACL 상속 게이트**(INVALID_ACL) → `analyze_attachment`
  (미통과 시 그 status 를 stage=ANALYZE 로 기록 후 스킵) → `chunk_attachment_fn`(ValueError 는
  `ATTACH_ENCRYPTED`/`UNSUPPORTED_ATTACH_TYPE` 로 매핑, stage=CHUNK) → `index_chunks`
  (`attachment_download_urls={id: download_url}`) → UPSERT SUCCESS. `chunk_attachment` 는 파일
  시스템 의존이라 `ChunkingWorkerDeps.chunk_attachment_fn` 으로 주입 가능(rag
  IngestionGraphDeps 패턴 정합). `AttachmentNotFoundError`, `ChunkingMessageResult.attachment_id`,
  `_record(stage, attachment_id)` 확장 추가.
- `app/ingestion/crawler.py` — `build_attachment_chunking_message(page, attachment)` +
  `run_full_crawl` 이 `page.attachments` 를 `save_attachment` + 첨부 `content.chunking`
  (`source_type=attachment`) 발행. 첨부 단위 적재·발행 실패는 `failed_attachment_ids` 로 격리
  (페이지·다른 첨부 무영향), `jobs` 주입 시 첨부 CRAWL SUCCESS 기록.
- `app/ingestion/pipeline.py` — `build_poc_components`/`run_poc_ingestion` 에 `chunk_attachment_fn`
  주입 파라미터(파일 시스템 없이 crawl→첨부 적재→첨부 청킹→Qdrant 전 체인 e2e).

**설계 메모**

- 첨부 메시지는 **본문과 같은 `content.chunking` 큐**를 공유하고 `source_type` 으로만 구분한다
  (신규 큐 미추가 — `QUEUE_ATTACHMENT` 는 예약 유지). 단일 Worker 가 양쪽을 소비한다.
- 첨부 청크의 멱등성은 본문과 동일하게 `(chunk_id, version_number)` 캐시로 보장된다
  (`version_by_page_id={page.page_id: page.version_number}`). 첨부 `chunk_id` 는
  `make_chunk_id(page_id, chunk_index, attachment_id)` 로 결정론적.
- ACL: 첨부는 부모 페이지 ACL 을 상속하므로(`build_attachment_metadata`), 부모가 INVALID_ACL
  이면 첨부도 색인하지 않는다.

**검증**: ruff check / ruff format / py_compile(app 전체 + 변경 테스트) 통과. 첨부 청킹 테스트는
`chunk_attachment_fn` fake 주입으로 외부 파일 의존성을 회피한다. 신규/확장 테스트:
`test_chunking_worker.py`(첨부 청킹·ACL 상속·미지원/저품질→ANALYZE·ATTACH_ENCRYPTED/기타
ValueError→CHUNK·멱등성·누락 첨부/부모 페이지·본문+첨부 혼합 디스패치),
`test_crawler.py`(첨부 적재·발행 메시지 형식·첨부 CRAWL 잡·실패 격리),
`test_pipeline_e2e.py`(첨부 전 체인 + 재실행 멱등성), `test_raw_store.py`(get_attachment).
**전체 pytest·`./scripts/verify.sh` 는 Mac/3.11** (샌드박스 Python 3.10 은 StrEnum 미지원).

**후속(TBD)**: ① 실 Confluence 첨부 **다운로드 어댑터**(Attachment API 바이너리 수집 →
`local_path`/`extracted_text` 채움, 인프라 의존). ② pika consumer/publisher 실행 loop
(featureI-7c 와 공통 — RabbitMQ 연결). vendored 에이전트 MVP 가 첨부를 수집하면 본 체인이
그대로 동작한다(현재 `attachments=[]`).
