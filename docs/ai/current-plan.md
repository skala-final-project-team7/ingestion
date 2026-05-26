# Current Plan — Data Ingestion Pipeline

이 문서는 현재 진행 중인 작업의 Plan을 기록한다. 구현 전에 작성하고, 작업 중 계획이 바뀌면 함께 수정한다.
하나의 feature가 끝나면 체크 처리하고, 모든 feature가 끝나면 새 세션에서 다음 Plan을 작성한다.

> **상태: 초기 스캐폴드 직후 (2026-05-26).** RAG 레포(`../rag`)에서 분리해 Data Ingestion Pipeline을
> 독립 저장소로 시작. 청킹·임베딩 자산은 복사 완료, 수집·동기화·큐 Worker는 신규 구현 예정.

---

## 작업 개요

- **작업 목표**: Confluence 문서·첨부 수집 → 추출 → 청킹 → 임베딩 색인 + 동기화까지 동작하는 수집 파이프라인 MVP
- **담당 영역**: Data Ingestion Pipeline 전체 (`app/`, `tests/`) — 요구사항정의서 **FR-001~FR-005**
  - FR-001 데이터 수집 에이전트(Confluence Page+첨부 수집) / FR-002 첨부 텍스트 추출기 /
    FR-003 문서·파일 유형 분류 + Adaptive Chunker / FR-004 Dual Embedding + Multi-Pool 색인 /
    FR-005 데이터 동기화 에이전트(Delta Sync + 3중 삭제). (FR-006~ 질의/응답·피드백·대시보드는 RAG/BFF 영역)
- **브랜치 규칙**: feature별로 `feat/#<이슈번호>/<기능-이름>`
- **근거 문서**: 외부 **요구사항정의서 v0.2.1**(§2.0 Multi-Agent / §2.2 FR-001~005 / §3 데이터 요구사항),
  **아키텍처 다이어그램**(Data Ingestion Pipeline: Data Sync/Ingestion Agent + Chunking + Embedding,
  RabbitMQ, Confluence·GPT 소스, MySQL/MongoDB/Qdrant, Logging&Monitoring),
  `docs/architecture.md`, `docs/rag-pipeline-design.md`(§3·§5·§7), `docs/chunking-strategy.md`,
  `docs/atlassian-api.md`(DATA-01~03), `docs/db-schema.md`

## 선행 확인 / 의존성

- [x] **RAG 레포 자산 복사** — schemas / chunker / embedder / embedding·vector_store·indexer /
  adapters / storage / attachment_analyzer / sync (2026-05-26, import 경로 그대로 미러링)
- [ ] **`pip install -e ".[ingestion,embedding,dev]"`** 후 import·테스트 통과 확인 (Mac/3.11)
- [ ] **RabbitMQ / MongoDB / Qdrant 로컬 기동**(docker compose) — Worker 통합 테스트 전 필요
- [ ] **Confluence access_token / cloudId 전달 경로** — BFF→Ingestion 전달 방식 확정 (요구사항정의서 §2-2)

---

## Milestone A — 스캐폴드·기반 (현재)

### featureI-1: 저장소 스캐폴드  ✅ 완료 (2026-05-26)

- [x] 디렉토리·git·pyproject·scripts·.gitignore
- [x] 문서(CLAUDE.md / architecture / conventions / db-schema / workflow / prompt-templates)
- [x] 청킹·임베딩·schemas·adapters·storage 자산 복사 + import 정합(schemas/__init__ 트림)
- [x] 신규 컴포넌트 stub (crawler / extractor / workers)
- [ ] `./scripts/verify.sh` 통과 확인 (Mac/3.11 — 의존성 설치 후)

## Milestone B — 수집 (FR-001 / FR-002)

### featureI-2: Data Ingestion Agent — Confluence Full Crawl (FR-001)  ✅ featureI-6 으로 구현

> **구현 경로**: 본 feature는 신규 작성이 아니라 **featureI-6(외부 에이전트 vendoring 통합)**으로 구현한다.
> Data Ingestion Agent를 무수정 vendoring하고 `AtlassianSourceAdapter`/`crawler.run_full_crawl` 어댑터로
> 잇는다. 아래 흐름·완료 기준은 그대로 유효하다.

- **작업 목표**: Confluence 전체 문서를 초기 수집(Full Crawl)해 `raw_pages`/`raw_attachments`(MongoDB)에
  적재하고 Chunking Queue로 인계한다. `DocumentSourceAdapter` 계약을 따르는 `AtlassianSourceAdapter`를
  신규 구현하고, `crawler.run_full_crawl`이 이를 오케스트레이션한다.
- **브랜치**: `feat/#2/confluence-full-crawl` (skeleton 브랜치와 분리 — 이슈번호는 팀 규칙 따름)
- **근거**: 요구사항정의서 FR-001, `docs/atlassian-api.md`(DATA-01 Full Crawl / DATA-03 Space 목록 /
  PageObject 매핑), `docs/architecture.md`, `docs/db-schema.md`.

#### 수집 흐름 (FR-001 1~8단계)

1. **Space 목록** — `DATA-03 GET /space`(사용자 접근 가능 Space만 자동 반환). 트리거 입력이 단일
   `spaceKey`(2단계 고정값)면 해당 스페이스만.
2. **페이지 Full Crawl** — Space별 `DATA-01 GET /content?type=page&spaceKey=...&expand=space,version,body.storage,metadata.labels,ancestors`
   를 `limit≤100` 페이지네이션 반복(`atlassian-python-api`의 `get_all_pages_from_space_as_generator`).
   ※ 요구사항의 homepage→descendants 트리 순회는 DATA-01 페이지네이션으로 대체(스페이스 전체 페이지 동등 수집).
3. **PageObject 매핑** — `docs/atlassian-api.md` 매핑표대로 id/title/body.storage→body_html/version/
   last_modified/space_key/labels/ancestors/webui_link/attachments[].
4. **ACL 적재** — ★ 미해결(아래 선행 결정). PoC는 `_synthesize_acl`(space_key 기반) 패턴 재사용.
5. **첨부 다운로드** — `attachments[]`의 다운로드 URL로 바이너리 수집(PDF/Word/Excel). 실패는 페이지
   전체 실패로 전파하지 않고 격리(graceful degrade) 후 기록.
6. **MongoDB 적재** — `raw_pages`(페이지 원본 JSON + PageObject) / `raw_attachments`(메타 + 바이너리 핸들).
7. **Chunking Queue 발행** — `content.chunking` 라우팅 키로 후속 메시지 발행(pika). 첨부는 FR-002로 라우팅.
8. **실패 처리** — Rate Limit(429) 지수 백오프(tenacity), 실패 페이지/첨부 재시도 또는 DLQ 보류.
   진행/결과(성공·실패·스킵·소요시간)는 `import_jobs`(MongoDB)에 기록.

#### 수정 대상 파일

- `app/adapters/atlassian.py` (신규) — `AtlassianSourceAdapter(DocumentSourceAdapter)`:
  `fetch_pages(since=None)`(None=Full Crawl, since=Delta는 FR-005에서 활용) / `list_active_ids`
  (Reconciliation용) / `watch_changes`(미지원 → 빈 스트림). `atlassian-python-api`로 DATA-01/03 호출,
  `access_token`+`cloudid` 주입, Rate Limit 백오프.
- `app/adapters/factory.py` — `source.type="atlassian"` 분기 추가(기존 json_fixture 분기 유지).
- `app/ingestion/crawler.py` (stub→구현) — `run_full_crawl(CrawlRequest)`: 어댑터 fetch_pages 순회 →
  raw 적재 → 첨부 다운로드 → 큐 발행 → `CrawlResult` 집계.
- `app/storage/raw_store.py` (신규) — `raw_pages`/`raw_attachments` 적재 헬퍼(기존 `mongo_cache` 패턴 재사용).
- `app/ingestion/workers/ingestion_worker.py` (신규) — Ingestion 큐 소비 + Chunking 큐 발행(pika).
- `app/storage/jobs.py` (기존 확장) — `import_jobs` 기록(현 `ingestion_jobs` 헬퍼 재사용/정합).
- `app/config.py` — Confluence base URL·cloudid 경로·`source.type` 설정.
- `docs/db-schema.md` — `raw_pages`/`raw_attachments`/`attachment_texts` 컬렉션 스키마 추가(현재 없음).
- `tests/adapters/test_atlassian.py`, `tests/ingestion/test_crawler.py` (신규).

#### 수정하지 않을 파일

- `app/ingestion/chunker/`·`embedder/`·`embedding.py`·`vector_store.py`·`indexer.py` (FR-003/004 — featureI-4)
- `app/ingestion/sync.py` (FR-005 — featureI-5), `app/ingestion/extractor/` (FR-002 — featureI-3)
- `app/schemas/*` (PageObject/Attachment 기존 활용 — 변경 불필요)

#### 선행 의존성 / 결정 필요

- [ ] ★ **ACL 모델 결정** — `docs/atlassian-api.md` "ACL 미해결 사항": Atlassian 명세에 페이지 단위
  권한 API가 없음. (a) `space_key` 기반(접근 가능 스페이스) vs (b) content restrictions API 추가 도입.
  PoC는 (a) `_synthesize_acl` 합성으로 진행하고, 실연동은 팀(+RAG 검색 ACL 필터)과 결정. **RAG 레포의 ACL 필터와 직결 — 공유 계약.**
- [ ] **`access_token`/`cloudId` 전달 경로** — Authorization Server→BFF→Ingestion 전달 방식 확정(요구사항 §2-2).
- [ ] **RabbitMQ / MongoDB 로컬 기동**(docker compose) — 통합 테스트 전.

#### 테스트 방법 (외부 의존성 mock/fake)

- mock HTTP로 DATA-03 Space 목록 → DATA-01 페이지 페이지네이션 순회, `_links.next`/`start` 반복.
- PageObject 매핑 정합(매핑표 전 필드), ACL 합성, 첨부 메타 매핑.
- `raw_pages`/`raw_attachments` 적재(fake Mongo), Chunking Queue 메시지 형식·라우팅 키(fake pika).
- Rate Limit 429 → 지수 백오프 재시도, 첨부 다운로드 실패 격리, 실패 페이지 DLQ.
- `crawler.run_full_crawl` end-to-end(어댑터+storage+queue 전부 fake) → `CrawlResult` 집계 검증.

#### 위험 요소

- ACL 미해결이 검색 단계 ACL 필터와 직결 — 결정 전까지 PoC 합성으로만 진행(실연동 재작업 가능).
- 대용량 Full Crawl 시간/메모리 — 제너레이터 스트리밍 + 배치 적재로 완화.
- API Rate Limit / 첨부 다운로드 불안정 — 백오프·격리·DLQ로 대응.

#### 완료 기준

- `AtlassianSourceAdapter`가 `DocumentSourceAdapter` 계약(3개 메서드) 충족 + 단위 테스트 통과.
- mock Full Crawl end-to-end(Space→페이지→raw 적재→Chunking Queue 발행) 통과.
- `./scripts/verify.sh`(format→lint→test) 통과.
- `docs/db-schema.md`에 `raw_pages`/`raw_attachments` 스키마 추가, `docs/ai/working-log.md` 기록.
- ACL/토큰 전달의 미해결 결정은 문서에 명시(추측 구현 금지).

### featureI-3: 첨부 텍스트 추출기 (FR-002)

- **목표**: `raw_attachments`의 PDF/Word/Excel을 텍스트로 추출(이미지·도형 제외) → `attachment_texts` 적재 →
  Chunking Queue(첨부) 발행. Excel/CSV는 시트→자연어 직렬화.
- 수정 대상: `app/ingestion/extractor/`, `app/ingestion/workers/`
- 재사용: chunker의 첨부 처리 로직(`chunker/attachment.py`)과 추출 책임 분리 정리
- 테스트: 파일 유형별 추출, 추출 실패 graceful degrade, 큐 메시지 형식

## Milestone C — 청킹·임베딩 Worker (FR-003 문서·파일 유형 분류 + Adaptive Chunker / FR-004 Dual Embedding 색인)

### featureI-4: 문서·파일 유형 분류 + Chunking / Embedding Worker (FR-003 / FR-004)  ✅ 구현 완료 (단일 Worker, 2026-05-26)

> **상태: 구현 완료 (단일 Worker 토폴로지).** `content.chunking` 소비 → `raw_pages.get_page` →
> `chunk_page`(doc_type 라벨 폴백) → `index_chunks`(Dual Embedding + Qdrant Multi-Pool upsert +
> `embedding_cache` 멱등성) → `ingestion_jobs`(stage UPSERT) 배선. 검증 ruff/mypy/Fake end-to-end
> 통과(전체 pytest·verify.sh 는 Mac). **후속**: featureI-4b(GPT-4o-mini 문서 분석기[Agent] +
> MySQL `space_doc_type_cache`), 첨부 청크 경로(FR-002 이후), 실 어댑터 부트스트랩 + pika consumer
> 배포 wiring. 신규: `workers/consumer.py`·`workers/chunking_worker.py`, `raw_store.get_page`,
> `tests/ingestion/test_chunking_worker.py`. 상세는 `docs/ai/working-log.md` 참조. (아래는 원 Plan.)

- **작업 목표**: featureI-6로 연결된 앞 절반(crawl → `raw_pages` 적재 → `content.chunking` 발행)을
  이어받아 **`content.chunking` 메시지를 소비 → Adaptive Chunker → Dual Embedding → Qdrant upsert**
  까지 배선해 수집 파이프라인을 end-to-end로 동작시킨다. chunker/embedder/embedding/vector_store/
  indexer 는 이미 복사돼 있고, **소비 Worker만 없다.**
- **브랜치**: `feat/#7/chunking-embedding-worker` (feat/#6 머지 후 분기 권장 — 스택 방지).
- **근거**: 요구사항정의서 FR-003/FR-004, `docs/architecture.md`, `docs/chunking-strategy.md`, `docs/db-schema.md` §1.

#### 핵심 설계 결정 (구현 전 확정 필요)

- ★ **Worker 토폴로지**: 복사된 `indexer.index_chunks` 가 **임베딩+upsert+cache 를 한 흐름으로 결합**한다.
  - **(A) 단일 Chunking+Embedding Worker (PoC 권장)**: `content.chunking` 소비 → `raw_pages` 로드 →
    `chunk_page` → `index_chunks`(embed+upsert). `content.embedding` 큐는 상수로 예약만. 부품이 적고
    Chunk 직렬화 불필요. 아키텍처 다이어그램의 4큐 중 Embedding 큐를 생략하는 절충.
  - **(B) 2-Worker (다이어그램 정합)**: Chunking Worker(→`content.embedding` 에 Chunk payload 발행) +
    Embedding Worker(Chunk 복원 → `index_chunks`). 다이어그램에 충실하나 Chunk 직렬화/역직렬화 필요.
  - → PoC는 (A)로 진행하고 (B)는 운영 스케일링 시 분리하도록 문서화 제안(확정은 구현 착수 시).
- **문서 분석기 [Agent]**: 우선 **결정론 폴백**(`chunker.body.infer_doc_type` 휴리스틱 / 'operation')로
  배선해 end-to-end 를 먼저 닫고, **GPT-4o-mini + Function Calling + MySQL `space_doc_type_cache`** 는
  후속 sub-feature(featureI-4b)로 분리. (rag 미구현 → 본 레포 신설.)

#### 수정/신규 대상 파일

- `app/ingestion/workers/chunking_worker.py` (신규) — `content.chunking` 소비 → raw_pages 로드 →
  doc_type 판별(폴백) → `chunk_page` → `index_chunks` → `ingestion_jobs` 기록(stage `chunk`/`embed`/
  `upsert` — **enum 이미 존재**, crawl 과 달리 기록 가능). 큐 소비 추상화(Consumer ABC + Fake + Pika).
- `app/ingestion/workers/__init__.py` — Consumer/Worker export.
- `app/storage/raw_store.py` (확장) — `get_page(page_id) -> PageObject | None` **읽기** 메서드 추가
  (현재 save 만 존재). Fake/Mongo 양쪽.
- `app/ingestion/document_analyzer.py` (신규, featureI-4b) — 문서 분석기 [Agent]. featureI-4 본편은
  폴백만, LLM 판별은 후속.
- 운영 의존성 빌더(실 E5/BM25 임베더 + Qdrant from_settings)는 `app/config.py` `use_real_adapters`
  토글 패턴 재사용. 테스트는 Fake 임베더 + in-memory Qdrant/Fake cache 주입.
- 첨부 경로(`chunk_attachment`)는 첨부 입력이 생기는 FR-002(featureI-3) 이후 연결 — 본편은 본문 청크만.

#### 테스트 (외부 의존성 mock/fake)

- end-to-end: `content.chunking` 메시지 → Worker → fake raw_store(get_page) → `chunk_page` →
  Fake 임베더 + Fake Qdrant + Fake cache → upsert 카운트/`chunk_id` 검증.
- 멱등성: 동일 `(chunk_id, version_number)` 재실행 시 `index_chunks` skip(캐시 히트) 검증.
- doc_type 폴백 분기, `ingestion_jobs` 단계별(chunk/embed/upsert) 기록, 메시지 형식 round-trip.

#### 완료 기준

- crawl → publish → **chunking_worker 소비 → Qdrant upsert** 가 fake end-to-end 로 통과(멱등성 포함).
- `./scripts/verify.sh` 통과, `docs/architecture.md`(Worker 상태)·`docs/ai/working-log.md` 갱신.
- LLM 문서 분석기·첨부 경로·`content.embedding` 분리는 후속(featureI-4b/featureI-3)으로 문서화.

#### 선행/TBD

- ACL·access_token/cloud_id 전달 경로는 featureI-6와 동일 TBD(검색 정확도/실연동 — 병렬 협의).
- 실 임베딩 모델(E5 ~2.4GB)·Qdrant 서버는 통합 테스트에만 필요. 단위/CI 는 Fake 로 대체.

### featureI-4b: 문서 분석기 [Agent] — GPT-4o-mini doc_type 판별 + MySQL 캐싱 (FR-003)  📋 진행 중

- **작업 목표**: featureI-4 의 라벨 휴리스틱 폴백을 대체해, **스페이스 단위 1회** 본문 doc_type 을
  6유형(incident/operation/faq/meeting/adr/troubleshoot)으로 LLM 판별하고 MySQL
  `space_doc_type_cache`(db-schema §3.1)에 캐싱한다. 이후 같은 스페이스의 모든 페이지가 캐시를 재사용.
- **브랜치**: `feat/#8/document-analyzer` (feat/#7 머지 후 분기 권장).
- **분류 [Agent] 규칙(app/CLAUDE.md §5)**: GPT-4o-mini, Function Calling 으로 스키마 강제, 타임아웃 +
  Fallback. 신뢰도 < 0.6 또는 LLM 실패 시 `DocType.OPERATION` 폴백(DocType 에 'general' 없음 — db-schema
  confidence 주석과 정합. CLAUDE.md 의 'general' 표기와의 차이는 문서화).
- **수정/신규 대상**:
  - `app/storage/space_doc_type_cache.py`(신규) — `SpaceDocTypeCache` ABC + `FakeSpaceDocTypeCache` +
    `MySQLSpaceDocTypeCache`(sqlalchemy) + `SpaceDocTypeEntry`. db-schema §3.1 정합.
  - `app/ingestion/document_analyzer.py`(신규 [Agent]) — `DocTypeClassifier` ABC + `FakeDocTypeClassifier`
    + `OpenAIDocTypeClassifier`(GPT-4o-mini, Function Calling, 타임아웃) + `DocumentAnalyzer.resolve_doc_type`
    (캐시 우선 → 미스 시 분류 → 캐싱 → 폴백). LLM 호출은 어댑터 경계에 격리(테스트는 Fake).
  - `app/ingestion/workers/chunking_worker.py`(확장) — `ChunkingWorkerDeps.doc_type_resolver`(optional)
    추가. 주입 시 `chunk_page(page, resolver.resolve_doc_type(page))`, 미주입 시 기존 라벨 폴백(무변).
  - `app/storage/__init__.py` export, (`app/config.py` 의 `llm_aux_model`/`openai_api_key`/`mysql_uri` 재사용).
- **테스트(외부 mock)**: 캐시 미스→분류→캐싱, 캐시 히트 재사용, 저신뢰/예외→OPERATION 폴백,
  Worker 가 resolver doc_type 으로 청킹. OpenAI·MySQL 은 Fake 로 대체.
- **완료 기준**: Worker 가 resolver 주입 시 LLM doc_type 으로 청킹 + 스페이스 1회 판별 캐싱. `verify.sh` 통과.
- **TBD**: 다중 샘플 스페이스 분석(PoC 는 첫 페이지 1샘플), 실 OpenAI/MySQL 부트스트랩 wiring.

## Milestone D — 데이터 동기화 에이전트 (FR-005)

### featureI-5: 데이터 동기화 에이전트 — Delta Sync + 3중 삭제 동기화 (FR-005)  ✅ featureI-6 으로 구현(Delta+Reconcile / Trash·Webhook TBD)

> **구현 경로**: 본 feature도 **featureI-6(외부 에이전트 vendoring 통합)**으로 구현한다. Data Sync Agent를
> 무수정 vendoring하고 `app/ingestion/sync.py`(기존 `reconcile_deletions` 보존) + 어댑터로 잇는다.

- **목표**: 주기(기본 1시간) Delta Sync — Confluence API로 Space/Page/첨부 메타 수집, MongoDB 원본
  (`version`/`updatedAt`) 비교로 변경·삭제 페이지만 식별 → 변경분만 FR-001~FR-004 동일 파이프라인 재투입
  (본문 재수집 → chunk 재생성 → Vector DB upsert).
- **3중 삭제 동기화**: (1) Confluence **Trash API**로 삭제(Trashed) 페이지·첨부 조회 → Qdrant payload
  `soft_delete` (소프트 삭제), (2) **Webhook**(실시간 삭제 이벤트), (3) 주 1회 **Reconciliation**(고스트 데이터 제거).
- 수정 대상: `app/ingestion/sync.py`(복사된 `reconcile_deletions` 확장), `app/ingestion/workers/`
- 재사용: `sync.py`의 `reconcile_deletions`(복사 완료, Reconciliation 중심)
- 테스트: 변경/삭제 식별, 고스트 삭제, Reconciliation 멱등성, Delta 재투입 흐름

---

## Milestone E — 외부 에이전트 2종 vendoring 통합 (featureI-6)  ✅ 구현 완료 (2026-05-26)

> **상태: 구현 완료 (2026-05-26).** Data Ingestion Agent(FR-001)·Data Sync Agent(FR-005)
> 두 패키지를 수신해 vendoring → 어댑터 → 큐 배선 → 테스트까지 완료. 검증은 ruff(통과) +
> py_compile 까지 샌드박스(Python 3.10)에서 수행. **전체 pytest·`./scripts/verify.sh`·push 는
> Mac(3.11)에서 수행 필요**(샌드박스는 vendored 의 StrEnum 미지원 + 의존성 미설치).
>
> **결정 결과**: (1) vendoring 레이아웃 = 패키지를 저장소 루트로(rag 미러), 에이전트
> `scripts/`+`tests/` 는 `tests/<agent>/` 에 무수정 + `__init__.py` 마커. (2) 어댑터 구동 =
> 에이전트 상단 workflow(`run_full_crawl_workflow`/`run_data_sync_workflow`) in-process
> 블랙박스 호출 + 산출물 메모리 변환(로컬 파일 출력은 임시 디렉토리로 우회).
>
> **수신 에이전트 실측 vs 계획 차이**: 에이전트 MVP 는 ① ACL ② labels/ancestors
> ③ 첨부(not_supported_in_mvp) 를 산출하지 않고, ④ 자체 `ProcessedDocument`(중첩
> space/page/body) 스키마를 쓴다. → 어댑터가 `PageObject` 로 변환하고 ACL 은 space_key 합성,
> ②③ 는 빈 값으로 둔다(모두 문서화된 TBD). 또한 ⑤ `IngestionStage` enum 에 crawl/ingest
> 값이 없어 crawl 단계 `ingestion_jobs` 기록은 보류(공유 enum 추가 = RAG 분기 영향 → 협의 대기),
> `CrawlResult`/`DeltaSyncResult` 를 잡 리포트로 사용. ⑥ snapshot 영속화는 에이전트 로컬
> 파일 기반(Mongo 영속화는 후속).
>
> 본 featureI-6은 featureI-2(Full Crawl)·featureI-5(Delta Sync + 3중 삭제)를 **"신규 작성"이
> 아니라 "외부 에이전트 vendoring + 얇은 어댑터"** 방식으로 구현한 상위 작업이다.
>
> **구현/수정 파일**: `data_ingestion_agent/`·`data_sync_agent/`(vendored), `tests/<agent>/`,
> `pyproject.toml`(include/exclude/agents extra), `app/adapters/atlassian.py`(신규),
> `app/adapters/factory.py`·`app/adapters/__init__.py`(atlassian 분기), `app/config.py`
> (atlassian placeholder), `app/storage/raw_store.py`(신규)·`app/storage/__init__.py`,
> `app/ingestion/workers/publisher.py`(신규), `app/ingestion/crawler.py`(구현),
> `app/ingestion/sync.py`(`run_delta_sync` 추가, `reconcile_deletions` 무수정),
> `tests/adapters/test_atlassian.py`·`tests/ingestion/test_crawler.py`·`test_sync.py`(신규),
> `tests/test_scaffold.py`(crawler stub 테스트 갱신), `docs/db-schema.md`·`docs/architecture.md`.

### 작업 목표

- 외부에서 받은 두 에이전트 패키지를 **무수정으로 저장소 루트에 vendoring**하고, ingestion 파이프라인은
  vendored 코드를 직접 호출하지 않고 `app/ingestion/`의 **얇은 어댑터**를 통해서만 연결한다.
- vendored 코드와 ingestion 계약(`PageObject`, `DocumentSourceAdapter`, 큐 메시지 형식)이 어긋나면
  **어댑터에서 변환**한다. vendored 원본은 절대 수정하지 않는다.
- 통합 완료 시 featureI-2(Full Crawl)·featureI-5(Delta Sync + 3중 삭제)의 완료 기준을 충족한다.

### 브랜치

- `feat/#6/vendor-ingestion-sync-agents` (이슈번호는 팀 규칙 — skeleton 브랜치와 분리)
- 1 change-set = 1 commit 지향. 규모가 크면 (1) vendoring+pyproject, (2) Ingestion Agent 어댑터,
  (3) Sync Agent 어댑터, (4) 큐 배선, (5) 테스트로 커밋을 분할한다.

### 1) Vendoring 레이아웃 (rag 미러링 — 반드시 준수)

```
ingestion/
├── data_ingestion_agent/      # ← 수신 패키지 무수정 vendoring (FR-001)
├── data_sync_agent/           # ← 수신 패키지 무수정 vendoring (FR-005)
├── app/                       # ingestion 본체 (어댑터가 vendored 호출)
└── tests/
    ├── data_ingestion_agent/  # 받은 테스트 무수정 배치 (+ __init__.py 만 허용)
    └── data_sync_agent/
```

- 받은 패키지 디렉토리명·내부 import 경로는 **그대로 보존**한다(원본 무수정). 실제 디렉토리명은
  수신 패키지에 맞춘다(`data_ingestion_agent`/`data_sync_agent`는 잠정명).
- 받은 테스트는 `tests/<agent>/`에 무수정 배치하되, pytest 패키지 인식용 `__init__.py`만 추가 허용한다.
- vendored 패키지가 자체 의존성을 요구하면 `pyproject.toml`에 **추가만** 하고 기존 의존성은 건드리지 않는다.

### 2) `pyproject.toml` 편집 (vendored 무수정 보존 장치)

```toml
[tool.setuptools.packages.find]
include = ["app*", "data_ingestion_agent*", "data_sync_agent*"]   # ← vendored 추가

[tool.ruff]
line-length = 100
target-version = "py311"
extend-exclude = ["data_ingestion_agent", "data_sync_agent"]      # ← lint 제외(원본 무수정)

[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
exclude = ["data_ingestion_agent/", "data_sync_agent/"]           # ← 타입체크 제외
```

- ruff/mypy 제외는 vendored 원본을 우리 컨벤션에 맞추려 수정하지 않기 위한 것이다(rag 패턴 동일).
- 어댑터(`app/ingestion/*`)는 **제외 대상이 아니다** — 우리 코드이므로 format/lint/type 전부 적용한다.

### 3) 어댑터 seam 설계 (vendored ↔ ingestion 계약)

#### (A) Data Ingestion Agent → Full Crawl (featureI-2 연계)

- **`app/adapters/atlassian.py` (신규)** — `AtlassianSourceAdapter(DocumentSourceAdapter)`.
  vendored Data Ingestion Agent를 in-process로 호출하고, 그 산출물을 표준 `PageObject` 스트림으로 변환한다.
  - `fetch_pages(since=None)`: `since=None`이면 vendored Full Crawl, `since`가 있으면 Delta 입력으로 위임.
  - `list_active_ids() -> ActiveIds`: Reconciliation용 살아있는 page_id/attachment_id 집합.
  - `watch_changes() -> Iterator[ChangeEvent]`: 미지원이면 빈 스트림.
  - vendored 출력 필드 ↔ `PageObject`(page_id/space_key/title/body_html/version_number/last_modified/
    labels/ancestors/webui_link/attachments) **매핑은 어댑터 내부에서** 수행(`docs/atlassian-api.md` 매핑표 기준).
  - **ACL**: vendored가 ACL을 제공하지 않으면 PoC `_synthesize_acl`(`["space:{space_key}"]`) 패턴으로 합성.
    `PageObject.is_acl_missing`이면 `INVALID_ACL`로 색인 제외(스키마 단 거부 금지).
  - **access_token/cloud_id**: `CrawlRequest`(crawler.py)에 이미 placeholder 필드 존재. 어댑터 생성자로
    주입하되 **로그·메시지·테스트 픽스처에 남기지 않는다**(app/CLAUDE.md §3).
- **`app/adapters/factory.py` (확장)** — `source_type=="atlassian"` 분기의 `NotImplementedError`를 제거하고
  `AtlassianSourceAdapter` 생성으로 교체(기존 `json_fixture` 분기 유지).
- **`app/ingestion/crawler.py` (stub→구현)** — `run_full_crawl(CrawlRequest)`가 어댑터를 오케스트레이션:
  `fetch_pages()` 순회 → `raw_pages`/`raw_attachments` 적재 → 첨부 다운로드(graceful degrade) →
  Chunking Queue(`content.chunking`) 발행 → `CrawlResult` 집계. 진행/결과는 `ingestion_jobs`에 기록.
- **`app/storage/raw_store.py` (신규)** — `raw_pages`/`raw_attachments` 적재 헬퍼(`mongo_cache`/`jobs.py`
  의 ABC + Fake + Mongo 3계층 패턴 재사용). featureI-2 plan 정합.

#### (B) Data Sync Agent → Delta Sync + 3중 삭제 (featureI-5 연계)

- **`app/ingestion/sync.py` (복사본 확장)** — 현재 `reconcile_deletions`(Reconciliation)만 존재.
  vendored Data Sync Agent를 어댑터로 연결해 Delta Sync(변경·삭제 페이지 식별 → 변경분만 FR-001~004
  재투입)와 3중 삭제(Trash API / Webhook / 주1회 Reconciliation)를 잇는다. 기존 `reconcile_deletions`
  시그니처·동작은 보존(비파괴 확장).
- vendored Sync 로직이 `DocumentSourceAdapter.fetch_pages(since=...)`/`list_active_ids()`/`watch_changes()`
  계약과 어긋나면 어댑터에서 변환한다. 삭제는 Qdrant payload `soft_delete`(소프트 삭제) →
  `store.delete_by_page_id`/`delete_by_attachment_id` cascade로 잇는다.
- **`app/ingestion/workers/sync_worker.py` (신규, featureI-5)** — 주기 트리거/Webhook 수신을 sync 어댑터에 배선.

#### (C) 공통 — 큐 배선

- 큐/라우팅 키 상수는 `app/ingestion/workers/__init__.py`의 `QUEUE_INGESTION="ingestion"` /
  `QUEUE_ATTACHMENT="content.extract.attachment"` / `QUEUE_CHUNKING="content.chunking"` /
  `QUEUE_EMBEDDING="content.embedding"`를 **그대로 사용**(신규 키 추가 금지, 필요 시 plan에서 합의).
- 발행 메시지 스키마(page_id/attachment_id/stage/라우팅 키)는 featureI-2/I-4 형식과 정합. pika 발행은
  fake로 테스트.

### 4) 수정하지 않을 파일

- `data_ingestion_agent/`·`data_sync_agent/` vendored 원본 전체(어댑터에서만 변환).
- `app/ingestion/chunker/`·`embedder/`·`embedding.py`·`vector_store.py`·`indexer.py`(FR-003/004 — featureI-4).
- `app/ingestion/extractor/`(FR-002 — featureI-3).
- `app/schemas/*`(PageObject/Attachment 계약 — 변경 불필요. 변경 필요 판단 시 RAG 분기 영향 먼저 설명).

### 5) 테스트 (외부 의존성 mock/fake)

- 받은 에이전트 테스트는 `tests/<agent>/`에 무수정 이식 후 통과 확인(Mac/3.11).
- 어댑터 신규 테스트(`tests/adapters/test_atlassian.py`, `tests/ingestion/test_crawler.py`,
  `tests/ingestion/test_sync.py`): vendored 출력 → `PageObject` 매핑 정합, ACL 합성, `is_acl_missing`→
  `INVALID_ACL`, 첨부 매핑, Chunking Queue 메시지 형식·라우팅 키(fake pika), `raw_*` 적재(fake Mongo),
  Delta since 필터, 3중 삭제(soft_delete/cascade), Reconciliation 멱등성.
- vendored 코드 자체의 단위 테스트는 작성하지 않는다(원본 책임). 우리는 **어댑터 경계만** 검증한다.

### 6) 검증 (이 샌드박스 제약 명시)

- 이 샌드박스는 Python 3.10이라 `StrEnum` 사용 코드의 pytest 실행이 불가하다 →
  여기서는 **ruff / py_compile / 정적 분석까지만** 수행하고, **전체 테스트(`./scripts/verify.sh`)·push는
  사용자가 Mac(3.11)에서** 수행한다. 커밋까지만 둔다(push 인증 정보 없음).

### 7) TBD — 협의 대기(추측 구현 금지)

- ★ **ACL 모델**: Atlassian 명세에 페이지 단위 권한 API 부재(`docs/atlassian-api.md` "ACL 미해결").
  PoC는 `space_key` 기반 `_synthesize_acl` 합성 유지. 실연동(space_key vs content restrictions)은
  팀(+RAG 검색 ACL 필터) 결정 대기 — **RAG 레포 공유 계약**.
- **`access_token`/`cloud_id` 전달 경로**(Auth Server→BFF→Ingestion): 미확정. `CrawlRequest` placeholder로 진행.
- 두 항목 모두 결정 전까지 문서에 TBD로 남기고 추측 구현하지 않는다.

### 8) 완료 기준

- 두 에이전트 패키지가 루트에 무수정 vendoring + `pyproject` include/제외 반영, vendored 테스트 통과.
- `AtlassianSourceAdapter`가 `DocumentSourceAdapter` 3개 메서드 충족, `crawler.run_full_crawl` mock
  end-to-end(Space→페이지→`raw_*` 적재→Chunking Queue 발행) 통과(featureI-2 완료 기준 충족).
- Sync 어댑터로 Delta Sync + 3중 삭제 흐름 mock 통과(featureI-5 완료 기준 충족).
- `./scripts/verify.sh`(Mac/3.11) 통과, `docs/db-schema.md`(`raw_pages`/`raw_attachments`/`attachment_texts`
  스키마)·`docs/architecture.md`·`docs/ai/working-log.md` 갱신, 토큰·자격증명 미포함 확인.

---

## 진행 규칙 (요약)

1. feature 단위로만 작업한다. 다음 feature는 새 세션 또는 `/clear` 후 시작한다.
2. 테스트 케이스 정리 → 실패 테스트 작성 → 최소 구현 → 테스트 통과 순서를 지킨다.
3. 완료 후 `./scripts/verify.sh`(format → lint → test)를 실행한다.
4. `git diff`로 변경 범위를 확인하고 커밋한다.
5. 외부 의존성(Confluence/Qdrant/Mongo/RabbitMQ)은 fake/mock으로 대체해 테스트한다.

---

## RAG 레포 공유 자산 메모

- 복사 자산(schemas/chunker/embedder/embedding/vector_store/indexer/adapters/storage)은 RAG 레포와
  origin이 같다. 공통 계약(Qdrant payload / embedding_cache 키 / ACL 필드)을 바꾸면 RAG 레포도 갱신 필요.
- 장기적으로 공유 패키지 분리 여부 검토(현재는 복사 유지).
