from __future__ import annotations

from data_ingestion_agent.extraction import HtmlExtractionResult, extract_storage_html


def test_heading_and_paragraph_are_extracted_as_plain_text() -> None:
    result = extract_storage_html("<h1>Runbook</h1><p>Restart the service.</p>")

    assert result.plain_text == "Runbook\nRestart the service."


def test_list_items_are_not_omitted() -> None:
    result = extract_storage_html(
        "<ul><li>Check logs</li><li>Restart worker</li></ul>"
        "<ol><li>Verify status</li></ol>"
    )

    assert "- Check logs" in result.plain_text
    assert "- Restart worker" in result.plain_text
    assert "1. Verify status" in result.plain_text


def test_table_cell_text_is_preserved() -> None:
    result = extract_storage_html(
        "<table>"
        "<tr><th>Owner</th><th>Status</th></tr>"
        "<tr><td>Platform</td><td>Ready</td></tr>"
        "</table>"
    )

    assert "Owner\tStatus" in result.plain_text
    assert "Platform\tReady" in result.plain_text


def test_anchor_uses_display_text() -> None:
    result = extract_storage_html(
        '<p>See <a href="https://example.invalid/runbook">the runbook</a>.</p>'
    )

    assert result.plain_text == "See the runbook."
    assert "https://example.invalid" not in result.plain_text


def test_html_entities_are_decoded() -> None:
    result = extract_storage_html("<p>Team A &amp; Team B&nbsp;handoff</p>")

    assert result.plain_text == "Team A & Team B handoff"


def test_script_and_style_content_is_removed() -> None:
    result = extract_storage_html(
        "<style>.hidden { display: none; }</style>"
        "<script>alert('token');</script>"
        "<p>Visible body</p>"
    )

    assert result.plain_text == "Visible body"
    assert "display" not in result.plain_text
    assert "alert" not in result.plain_text


def test_duplicate_whitespace_and_blank_lines_are_normalized() -> None:
    result = extract_storage_html(
        "<h2>  Title   With   Spaces </h2>"
        "<p>\n\nFirst&nbsp;&nbsp; paragraph</p>"
        "<p>Second     paragraph</p>"
    )

    assert result.plain_text == "Title With Spaces\nFirst paragraph\nSecond paragraph"


def test_empty_and_none_html_are_safe() -> None:
    empty_result = extract_storage_html("")
    none_result = extract_storage_html(None)

    assert empty_result == HtmlExtractionResult(storage_html="", plain_text="")
    assert none_result == HtmlExtractionResult(storage_html="", plain_text="")


def test_malformed_html_does_not_raise() -> None:
    result = extract_storage_html("<h1>Open<p>Paragraph <strong>bold")

    assert result.plain_text == "Open\nParagraph bold"


def test_confluence_macro_attachment_and_image_do_not_fail_extraction() -> None:
    storage_html = (
        "<p>Before macro</p>"
        '<ac:structured-macro ac:name="toc">'
        "<ac:parameter ac:name=\"maxLevel\">2</ac:parameter>"
        "</ac:structured-macro>"
        "<p>After macro</p>"
        '<ri:attachment ri:filename="synthetic.pdf" />'
        '<ac:image><ri:attachment ri:filename="diagram.png" /></ac:image>'
    )

    result = extract_storage_html(storage_html)

    assert "Before macro" in result.plain_text
    assert "2" in result.plain_text
    assert "After macro" in result.plain_text


def test_storage_html_is_preserved_separately_from_plain_text() -> None:
    storage_html = "<p>Original&nbsp;<strong>HTML</strong></p>"

    result = extract_storage_html(storage_html)

    assert result.storage_html == storage_html
    assert result.plain_text == "Original HTML"
