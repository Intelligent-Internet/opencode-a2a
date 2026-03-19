from opencode_a2a_server.text_parts import extract_text_from_parts


def test_extract_text_from_parts_falls_back_to_latest_snapshot() -> None:
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

    assert extract_text_from_parts(parts) == "final answer"


def test_extract_text_from_parts_prefers_text_parts_over_snapshots() -> None:
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
