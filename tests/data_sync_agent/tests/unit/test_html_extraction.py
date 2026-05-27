from __future__ import annotations

from data_sync_agent.extraction.html_extractor import extract_storage_html


def test_extract_preserves_double_encoded_entities() -> None:
    # 회귀(A): HTMLParser(convert_charrefs=True) 가 이미 한 번 디코딩하므로, handle_data 에서
    # 또 unescape 하면 이중 디코딩된다. storage HTML 의 ``&amp;lt;div&amp;gt;`` 는 화면에
    # 리터럴 ``<div>`` 를 표시하려는 의도(표시상 ``&lt;div&gt;``)인데, 이중 디코딩되면
    # ``<div>`` 로 손상된다. 한 번만 디코딩되어 ``&lt;div&gt;`` 가 보존되어야 한다.
    result = extract_storage_html("<p>Use &amp;lt;div&amp;gt; for a block.</p>")

    assert "&lt;div&gt;" in result.plain_text
    assert "<div>" not in result.plain_text


def test_extract_decodes_normal_entities_once() -> None:
    # 무회귀: 단일 인코딩 엔티티는 정상적으로 한 번만 디코딩된다.
    result = extract_storage_html("<p>a &amp; b</p>")

    assert "a & b" in result.plain_text


def test_extract_returns_storage_html_unchanged() -> None:
    # 원문 storage HTML 은 보존된다(추출은 plain_text 만 가공).
    html = "<p>hello &amp;lt;x&amp;gt;</p>"
    result = extract_storage_html(html)

    assert result.storage_html == html
