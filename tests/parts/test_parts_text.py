from opencode_a2a.parts.text import extract_text_from_parts


def test_extract_text_from_parts_returns_empty_for_non_list_input() -> None:
    assert extract_text_from_parts({"type": "text", "text": "ignored"}) == ""


def test_extract_text_from_parts_ignores_snapshot_parts() -> None:
    parts = [
        {
            "type": "step-start",
            "snapshot": "partial answer",
        },
        {
            "type": "step-finish",
            "snapshot": "final answer",
        },
    ]

    assert extract_text_from_parts(parts) == ""


def test_extract_text_from_parts_returns_text_parts_only() -> None:
    parts = [
        {
            "type": "step-finish",
            "snapshot": "snapshot answer",
        },
        {
            "type": "text",
            "text": "final answer",
        },
    ]

    assert extract_text_from_parts(parts) == "final answer"


def test_extract_text_from_parts_merges_text_and_reasoning_parts() -> None:
    parts = [
        "skip-me",
        {
            "type": "reasoning",
            "text": " draft",
        },
        {
            "type": "text",
            "text": " answer ",
        },
        {
            "type": "text",
            "text": 123,
        },
    ]

    assert extract_text_from_parts(parts) == "draft answer"
