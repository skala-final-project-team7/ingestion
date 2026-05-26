# LINA Data Ingestion Pipeline

척척학사(LINA) Confluence 기반 RAG 챗봇 서비스의 **데이터 수집 파이프라인**.
Confluence 문서·첨부를 수집(Full Crawl / Delta Sync)하여 첨부 텍스트 추출 → Adaptive Chunking →
Dual Embedding 색인까지 수행한다. RAG Pipeline(질의/응답, `../rag`)과 분리된 독립 배포 단위이며,
RabbitMQ 기반 비동기 파이프라인으로 동작한다.

## 구성

| 단계 | FR | 모듈 |
|---|---|---|
| Confluence Full Crawl | FR-001 | `app/ingestion/crawler.py` |
| Delta Sync / 삭제 동기화 | — | `app/ingestion/sync.py` |
| 첨부 텍스트 추출 (PDF/Word/Excel) | FR-002 | `app/ingestion/extractor/` |
| Adaptive Chunking (본문 6 + 첨부 3유형) | FR-003 | `app/ingestion/chunker/` |
| Dual Embedding 색인 (Dense+Sparse, Qdrant Multi-Pool) | FR-004 | `app/ingestion/embedder/`, `embedding.py`, `vector_store.py`, `indexer.py` |
| RabbitMQ Worker | — | `app/ingestion/workers/` |

자세한 흐름은 `docs/architecture.md`, 진행 계획은 `docs/ai/current-plan.md` 참조.

## 개발 환경

```bash
python -m venv .venv && source .venv/bin/activate   # Python 3.11
pip install -e ".[ingestion,embedding,dev]"
```

## 검증

```bash
./scripts/format.sh   # ruff format
./scripts/lint.sh     # ruff check
./scripts/test.sh     # pytest
./scripts/verify.sh   # format → lint → test
```

## 비고

- `app/schemas`·`app/ingestion/chunker`·`embedder`·`embedding.py`·`vector_store.py`·`indexer.py`·
  `app/adapters`·`app/storage` 는 RAG 레포(`../rag`)에서 복사한 공유 자산이다. 공통 계약(Qdrant payload /
  `embedding_cache` 키 / ACL 필드) 변경 시 RAG 레포와 함께 갱신한다.
- Confluence OAuth access token / cloudId 등 민감 정보는 로그·메시지 페이로드에 남기지 않는다.
