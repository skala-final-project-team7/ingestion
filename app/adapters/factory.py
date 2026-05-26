"""Document Source Adapter 팩토리.

--------------------------------------------------
작성자 : 최태성
작성목적 : ``Settings.source_type``과 ``Settings.samples_dir`` 등 환경 의존 값을 어댑터
          생성자에 일관되게 주입한다. 그래프 조립·CLI 진입점은 본 팩토리만 호출하고
          어댑터 클래스를 직접 인스턴스화하지 않는다.
작성일 : 2026-05-17
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-17, 최초 작성, 코드 리뷰 후속(P1-1) — Settings.samples_dir이 어댑터에
    흐르도록 build_source_adapter 도입
--------------------------------------------------
[호환성]
  - Python 3.11.x
--------------------------------------------------
"""

from app.adapters.base import DocumentSourceAdapter
from app.adapters.json_fixture import JsonFixtureSourceAdapter
from app.config import Settings, get_settings


class UnsupportedSourceTypeError(ValueError):
    """``Settings.source_type``이 지원하지 않는 값일 때 발생한다."""


def build_source_adapter(settings: Settings | None = None) -> DocumentSourceAdapter:
    """``Settings.source_type``에 따라 Document Source Adapter를 생성한다.

    Args:
        settings: 환경 설정. None이면 ``get_settings()``로 프로세스 단일 인스턴스를 쓴다.

    Returns:
        활성화된 ``DocumentSourceAdapter`` 구현체.

    Raises:
        UnsupportedSourceTypeError: ``source_type``이 지원하지 않는 값일 때.
        NotImplementedError: ``atlassian`` 어댑터가 아직 구현되지 않은 단계(현재 PoC).
    """
    resolved = settings or get_settings()
    source_type = resolved.source_type.lower()
    if source_type == "json_fixture":
        return JsonFixtureSourceAdapter(samples_dir=resolved.samples_dir)
    if source_type == "atlassian":
        # docs/ai/current-plan.md feature2 — access_token/cloudid 전달 경로 확정 후 구현.
        raise NotImplementedError(
            "AtlassianSourceAdapter는 access_token/cloudid 전달 경로 확정 후 구현된다 "
            "(docs/ai/current-plan.md feature2)"
        )
    raise UnsupportedSourceTypeError(
        f"지원하지 않는 source_type: {source_type!r} (json_fixture | atlassian)"
    )
