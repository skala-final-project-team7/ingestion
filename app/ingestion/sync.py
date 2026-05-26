"""삭제 동기화 [Pipeline] — Reconciliation (feature6 Phase 3).

--------------------------------------------------
작성자 : 최태성
작성목적 : Delta Sync 가 감지하지 못하는 삭제된 페이지·첨부를 Qdrant 에서 제거하기
          위한 Reconciliation 함수. 설계서 §3.7 Phase 1 흐름 정합 ─ source.
          list_active_ids() 와 Qdrant 적재 ID 의 차집합을 ghost 로 산출해 cascade
          삭제한다. PoC 단계는 본 Reconciliation 만 활성, 운영 전환 시 Trash API
          Sync + Webhook 리스너를 추가해 ‘주 1회 → 1시간 → 즉시’ 3중 안전망으로
          단축한다 (설계서 §3.7).
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature6 Phase 3 — ReconciliationResult 값 객체 +
    reconcile_deletions 함수. 7단계 흐름 정합. jobs 적재·스케줄링·알림은 호출자
    책임으로 분리.
--------------------------------------------------
[호환성]
  - Python 3.11.x
  - 외부 의존성 0 (주입된 DocumentSourceAdapter / QdrantPoolStore 가 외부 의존성을
    갖는다)
--------------------------------------------------
"""

from dataclasses import dataclass

from app.adapters.base import DocumentSourceAdapter
from app.storage.qdrant_client import QdrantPoolStore


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Reconciliation 실행 결과 — 호출자(스케줄러·그래프 노드)가 jobs 적재·알림에 사용.

    Attributes:
        deleted_pages: 삭제된 ghost 페이지의 ``page_id`` 목록.
        deleted_attachments: 삭제된 ghost 첨부의 ``attachment_id`` 목록.
    """

    deleted_pages: list[str]
    deleted_attachments: list[str]


def reconcile_deletions(
    *,
    source: DocumentSourceAdapter,
    store: QdrantPoolStore,
) -> ReconciliationResult:
    """source.list_active_ids() 와 Qdrant 적재 ID 의 차집합 ghost 를 cascade 삭제한다.

    설계서 §3.7 Phase 1 흐름 7단계:
        1. ``active_ids = source.list_active_ids()`` — {'pages': set, 'attachments': set}
        2. ``set_B_pages = store.scroll_page_ids()`` — CONTENT_POOL 의 본문 청크 page_id
        3. ``set_B_attaches = store.scroll_attachment_ids()`` — 첨부 청크 attachment_id
        4. ``ghost_pages = set_B_pages - active_ids.pages``
        5. ``ghost_attaches = set_B_attaches - active_ids.attachments``
        6. 각 ghost id 에 대해 ``store.delete_by_page_id`` / ``delete_by_attachment_id``
           호출 — 어댑터가 3 Pool 모두에서 cascade 삭제.
        7. ``ReconciliationResult`` 로 결과 반환 (호출자가 jobs 적재).

    ghost 가 0 이면 delete 호출 자체를 회피한다 — 운영 비용 절감 + false positive
    차단.

    Note:
        설계서 §3.7 의 cascade 모델 — ``set_B_pages`` 는 ``source_type=page`` 청크에서만
        추출되므로, **첨부만 적재되고 본문이 없는 페이지의 ``page_id`` 는 page-level
        ghost 로 잡히지 않는다.** 그 경우 attachment-level scroll 이 별도로
        attachment_id 를 처리한다 (본문 없는 페이지의 첨부는 attachment_id 기준 단독
        reconciliation). 운영에서는 본문 + 첨부가 함께 적재되는 시나리오가 정상이라
        본 모델이 일관성을 깨지 않는다 (설계서 §3.1 + §3.7 정합).

    Args:
        source: 공급원 어댑터. ``list_active_ids`` 만 호출한다.
        store: Qdrant Multi-Pool 저장소. scroll 2종 + delete 2종을 호출한다.

    Returns:
        삭제된 page_id / attachment_id 목록을 담은 ``ReconciliationResult``.
    """
    active_ids = source.list_active_ids()
    stored_page_ids = store.scroll_page_ids()
    stored_attachment_ids = store.scroll_attachment_ids()

    ghost_page_ids = stored_page_ids - active_ids.pages
    ghost_attachment_ids = stored_attachment_ids - active_ids.attachments

    # ghost set 을 정렬해 결정론적 결과를 반환 — 테스트·로깅 안정성.
    deleted_pages = sorted(ghost_page_ids)
    deleted_attachments = sorted(ghost_attachment_ids)

    for page_id in deleted_pages:
        store.delete_by_page_id(page_id)
    for attachment_id in deleted_attachments:
        store.delete_by_attachment_id(attachment_id)

    return ReconciliationResult(
        deleted_pages=deleted_pages,
        deleted_attachments=deleted_attachments,
    )
