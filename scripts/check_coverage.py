#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

OVERALL_MINIMUM = 90.0
PER_FILE_MINIMUMS = {
    "src/opencode_a2a_server/execution/executor.py": 90.0,
    "src/opencode_a2a_server/server/application.py": 90.0,
    "src/opencode_a2a_server/jsonrpc/application.py": 85.0,
    "src/opencode_a2a_server/opencode_client.py": 85.0,
}


def _read_coverage_percent(summary: dict[str, object]) -> float:
    value = summary.get("percent_covered")
    if not isinstance(value, int | float):
        raise ValueError(f"Unsupported coverage summary payload: {summary!r}")
    return float(value)


def main() -> int:
    coverage_json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".coverage.json")
    if not coverage_json_path.is_file():
        print(f"Coverage report not found: {coverage_json_path}", file=sys.stderr)
        return 1

    report = json.loads(coverage_json_path.read_text())
    totals = report.get("totals")
    files = report.get("files")
    if not isinstance(totals, dict) or not isinstance(files, dict):
        print(f"Unexpected coverage report shape in {coverage_json_path}", file=sys.stderr)
        return 1

    failures: list[str] = []
    overall_coverage = _read_coverage_percent(totals)
    if overall_coverage < OVERALL_MINIMUM:
        failures.append(
            f"total coverage {overall_coverage:.2f}% is below required {OVERALL_MINIMUM:.2f}%"
        )

    for relative_path, minimum in PER_FILE_MINIMUMS.items():
        file_report = files.get(relative_path)
        if not isinstance(file_report, dict):
            failures.append(f"missing coverage entry for {relative_path}")
            continue
        summary = file_report.get("summary")
        if not isinstance(summary, dict):
            failures.append(f"missing coverage summary for {relative_path}")
            continue
        file_coverage = _read_coverage_percent(summary)
        if file_coverage < minimum:
            failures.append(
                f"{relative_path} coverage {file_coverage:.2f}% is below required {minimum:.2f}%"
            )

    if failures:
        print("Coverage policy failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print(
        "Coverage policy satisfied: "
        f"total {overall_coverage:.2f}% and {len(PER_FILE_MINIMUMS)} critical file thresholds"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
