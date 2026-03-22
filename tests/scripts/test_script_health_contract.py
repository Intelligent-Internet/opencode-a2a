from pathlib import Path

DOCTOR_TEXT = Path("scripts/doctor.sh").read_text()
DEPENDENCY_HEALTH_TEXT = Path("scripts/dependency_health.sh").read_text()
HEALTH_COMMON_TEXT = Path("scripts/health_common.sh").read_text()
SMOKE_TEST_TEXT = Path("scripts/smoke_test_built_cli.sh").read_text()
COVERAGE_GATE_TEXT = Path("scripts/check_coverage.py").read_text()
SCRIPTS_INDEX_TEXT = Path("scripts/README.md").read_text()
PYPROJECT_TEXT = Path("pyproject.toml").read_text()


def test_shared_repo_health_prerequisites_live_in_common_helper() -> None:
    assert "run_shared_repo_health_prerequisites()" in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] sync locked environment"' in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] verify dependency compatibility"' in HEALTH_COMMON_TEXT
    assert "uv sync --all-extras --frozen" in HEALTH_COMMON_TEXT
    assert "uv pip check" in HEALTH_COMMON_TEXT


def test_doctor_keeps_local_regression_scope() -> None:
    assert 'source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"' in DOCTOR_TEXT
    assert 'run_shared_repo_health_prerequisites "doctor"' in DOCTOR_TEXT
    assert "uv run pre-commit run --all-files" in DOCTOR_TEXT
    assert "uv run pytest" in DOCTOR_TEXT
    assert "uv run python ./scripts/check_coverage.py" in DOCTOR_TEXT
    assert "uv pip list --outdated" not in DOCTOR_TEXT
    assert "uv run pip-audit" not in DOCTOR_TEXT


def test_dependency_health_keeps_dependency_review_scope() -> None:
    assert (
        'source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"'
        in DEPENDENCY_HEALTH_TEXT
    )
    assert 'run_shared_repo_health_prerequisites "dependency-health"' in DEPENDENCY_HEALTH_TEXT
    assert "uv pip list --outdated" in DEPENDENCY_HEALTH_TEXT
    assert "uv run pip-audit" in DEPENDENCY_HEALTH_TEXT
    assert "uv run pytest" not in DEPENDENCY_HEALTH_TEXT
    assert "uv run pre-commit run --all-files" not in DEPENDENCY_HEALTH_TEXT


def test_scripts_index_documents_split_health_entrypoints() -> None:
    assert "local development regression entrypoint" in SCRIPTS_INDEX_TEXT
    assert "dependency review entrypoint" in SCRIPTS_INDEX_TEXT
    assert "health_common.sh" in SCRIPTS_INDEX_TEXT


def test_smoke_test_requires_explicit_wheel_selection_when_dist_is_ambiguous() -> None:
    assert 'if [[ "$#" -gt 1 ]]; then' in SMOKE_TEST_TEXT
    assert (
        'artifact_path="${1:-${SMOKE_TEST_ARTIFACT_PATH:-${SMOKE_TEST_WHEEL_PATH:-}}}"'
        in SMOKE_TEST_TEXT
    )
    assert (
        "Multiple built wheels found; pass an explicit artifact path or set "
        "SMOKE_TEST_ARTIFACT_PATH." in SMOKE_TEST_TEXT
    )
    assert 'uv tool install "${artifact_path}" --python "${python_bin}"' in SMOKE_TEST_TEXT


def test_coverage_policy_tracks_overall_and_critical_file_thresholds() -> None:
    assert "OVERALL_MINIMUM = 90.0" in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/execution/executor.py": 90.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/server/application.py": 90.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/jsonrpc/application.py": 85.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/opencode_upstream_client.py": 85.0' in COVERAGE_GATE_TEXT
    assert "--cov-fail-under=90" in PYPROJECT_TEXT
    assert "--cov-report=json:.coverage.json" in PYPROJECT_TEXT
