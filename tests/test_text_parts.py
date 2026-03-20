from opencode_a2a_server.text_parts import extract_text_from_parts


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
