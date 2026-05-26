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

### featureI-2: Data Ingestion Agent — Confluence Full Crawl (FR-001)

- **목표**: Atlassian REST로 (1) 접근 가능 Space 목록 → (2) homepageId → (3) descendants 트리 순회,
  (4) Page 본문·메타·ACL(allowed_groups/allowed_users) 수집, (5) 첨부(PDF/Word/Excel) 다운로드,
  (6) `raw_pages`/`raw_attachments`(MongoDB) 적재, (7) Chunking Queue 발행, (8) 실패 재시도/DLQ.
- 수정 대상: `app/ingestion/crawler.py`, `app/adapters/atlassian.py`(신규), `app/storage/`, `app/ingestion/workers/`
- 의존: Confluence token 전달 경로, RabbitMQ/Mongo 기동
- 테스트: mock HTTP로 Space→descendants 순회, ACL 적재, 큐 메시지 형식, Rate Limit 백오프

### featureI-3: 첨부 텍스트 추출기 (FR-002)

- **목표**: `raw_attachments`의 PDF/Word/Excel을 텍스트로 추출(이미지·도형 제외) → `attachment_texts` 적재 →
  Chunking Queue(첨부) 발행. Excel/CSV는 시트→자연어 직렬화.
- 수정 대상: `app/ingestion/extractor/`, `app/ingestion/workers/`
- 재사용: chunker의 첨부 처리 로직(`chunker/attachment.py`)과 추출 책임 분리 정리
- 테스트: 파일 유형별 추출, 추출 실패 graceful degrade, 큐 메시지 형식

## Milestone C — 청킹·임베딩 Worker (FR-003 문서·파일 유형 분류 + Adaptive Chunker / FR-004 Dual Embedding 색인)

### featureI-4: 문서·파일 유형 분류 + Chunking / Embedding Worker (FR-003 / FR-004)

- **FR-003 — 문서·파일 유형 분류 + Adaptive Chunker**:
  - 문서 분석기 [Agent] (신규 구현, GPT-4o-mini + Function Calling): 본문을 6유형(장애대응/운영가이드/
    FAQ/회의록/ADR/트러블슈팅)으로 분류, 스페이스 단위 1회 판별 → MySQL `space_doc_type_cache` 캐싱
    (실패 시 'general' 폴백). **rag에서는 미구현(Agent 담당자 몫)이라 복사 자산에 없음 → 본 레포에서 신설.**
  - 첨부 분석기 [Pipeline] (복사 완료, `attachment_analyzer.py`): 파일 유형(PDF/Word/Excel) 기준 청킹 전략 분기.
  - Adaptive Chunker [Pipeline] (복사 완료, `chunker/`): 유형별 적합 전략 + 크기·오버랩 동적 조절.
- **FR-004 — Dual Embedding + Multi-Pool 색인** (복사 완료, `embedder/`·`embedding.py`·`vector_store.py`·`indexer.py`):
  Dense(e5-large 1024d) + Sparse(BM25), Qdrant Title/Content/Label Pool upsert, `embedding_cache` 멱등성.
- **Worker 배선**: Chunking Queue(`content.chunking`) 수신 → 문서/첨부 분석 → 청킹 → Embedding Queue 발행 →
  임베딩 → Qdrant upsert. 결과는 `import_jobs`(MongoDB) 기록.
- 수정 대상: `app/ingestion/document_analyzer.py`(신규 Agent), `app/ingestion/workers/`, (FR-004 복사 자산 재사용)
- 테스트: 문서 분석기(mock LLM, doc_type 판별·캐싱·폴백), Worker end-to-end(fake 큐/Qdrant/Mongo), 멱등성(동일 version skip)

## Milestone D — 데이터 동기화 에이전트 (FR-005)

### featureI-5: 데이터 동기화 에이전트 — Delta Sync + 3중 삭제 동기화 (FR-005)

- **목표**: 주기(기본 1시간) Delta Sync — Confluence API로 Space/Page/첨부 메타 수집, MongoDB 원본
  (`version`/`updatedAt`) 비교로 변경·삭제 페이지만 식별 → 변경분만 FR-001~FR-004 동일 파이프라인 재투입
  (본문 재수집 → chunk 재생성 → Vector DB upsert).
- **3중 삭제 동기화**: (1) Confluence **Trash API**로 삭제(Trashed) 페이지·첨부 조회 → Qdrant payload
  `soft_delete` (소프트 삭제), (2) **Webhook**(실시간 삭제 이벤트), (3) 주 1회 **Reconciliation**(고스트 데이터 제거).
- 수정 대상: `app/ingestion/sync.py`(복사된 `reconcile_deletions` 확장), `app/ingestion/workers/`
- 재사용: `sync.py`의 `reconcile_deletions`(복사 완료, Reconciliation 중심)
- 테스트: 변경/삭제 식별, 고스트 삭제, Reconciliation 멱등성, Delta 재투입 흐름

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
