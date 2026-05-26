"""app.ingestion.workers — RabbitMQ 단계별 Worker (비동기 수집 파이프라인) [stub].

각 Worker는 사용자 요청 트래픽과 분리된 RabbitMQ 큐를 소비해 한 단계를 처리하고 다음 큐로
메시지를 발행한다. EKS에서 독립 스케일링한다(요구사항정의서 §2.3, docs/architecture.md).

    Ingestion Queue   → ingestion_worker     (FR-001 크롤 결과 적재 후 첨부추출/청킹 라우팅)
    Attachment Queue  → attachment_worker    (FR-002 첨부 텍스트 추출)
    Chunking Queue    → chunking_worker       (FR-003 문서/첨부 분석 + Adaptive Chunking)
    Embedding Queue   → embedding_worker      (FR-004 Dual Embedding + Qdrant upsert)

큐/라우팅 키·DLQ 정책·메시지 스키마는 featureI-2~4 에서 확정한다(Quorum Queue 권장).
복사된 청킹/임베딩 자산(`app/ingestion/chunker`·`embedder`·`indexer.py`)을 Worker 경계에서 호출한다.
"""

QUEUE_INGESTION = "ingestion"
QUEUE_ATTACHMENT = "content.extract.attachment"
QUEUE_CHUNKING = "content.chunking"
QUEUE_EMBEDDING = "content.embedding"

__all__ = [
    "QUEUE_ATTACHMENT",
    "QUEUE_CHUNKING",
    "QUEUE_EMBEDDING",
    "QUEUE_INGESTION",
]
