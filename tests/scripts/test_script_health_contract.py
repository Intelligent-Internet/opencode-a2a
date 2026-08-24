from pathlib import Path

DOCTOR_TEXT = Path("scripts/doctor.sh").read_text()
CONFORMANCE_TEXT = Path("scripts/conformance.sh").read_text()
DEPENDENCY_HEALTH_TEXT = Path("scripts/dependency_health.sh").read_text()
HEALTH_COMMON_TEXT = Path("scripts/health_common.sh").read_text()
SMOKE_TEST_TEXT = Path("scripts/smoke_test_built_cli.sh").read_text()
COVERAGE_GATE_TEXT = Path("scripts/check_coverage.py").read_text()
THIN_WRAPPER_FINDER_TEXT = Path("scripts/find_thin_wrappers.py").read_text()
SCRIPTS_INDEX_TEXT = Path("scripts/README.md").read_text()
PYPROJECT_TEXT = Path("pyproject.toml").read_text()
DEPENDABOT_TEXT = Path(".github/dependabot.yml").read_text()


def test_shared_repo_health_prerequisites_live_in_common_helper() -> None:
    assert "run_shared_repo_health_prerequisites()" in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] sync locked environment"' in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] verify dependency compatibility"' in HEALTH_COMMON_TEXT
    assert "uv sync --all-extras --frozen" in HEALTH_COMMON_TEXT
    assert "uv pip check" in HEALTH_COMMON_TEXT


def test_doctor_keeps_local_regression_scope() -> None:
    assert 'source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"' in DOCTOR_TEXT
    assert 'run_shared_repo_health_prerequisites "doctor"' in DOCTOR_TEXT
    assert "doctor_repo_state_fingerprint()" in DOCTOR_TEXT
    assert "run_doctor_fix_phase()" in DOCTOR_TEXT
    assert "run_doctor_verify_phase()" in DOCTOR_TEXT
    assert "run_doctor_package_phase()" in DOCTOR_TEXT
    assert 'echo "[doctor] run fix phase"' in DOCTOR_TEXT
    assert 'echo "[doctor] run verify phase"' in DOCTOR_TEXT
    assert 'echo "[doctor] run package phase"' in DOCTOR_TEXT
    assert "uv run pre-commit run --all-files" in DOCTOR_TEXT
    assert "pre-commit modified files; review the changes and rerun doctor" in DOCTOR_TEXT
    assert "uv run mypy src/opencode_a2a" in DOCTOR_TEXT
    assert "uv run pytest" in DOCTOR_TEXT
    assert "uv run python ./scripts/check_coverage.py" in DOCTOR_TEXT
    assert "uv build --no-sources" in DOCTOR_TEXT
    assert "bash ./scripts/smoke_test_built_cli.sh dist/opencode_a2a-*.whl" in DOCTOR_TEXT
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
    assert "repository-owned A2A 1.0 black-box compatibility probes" in SCRIPTS_INDEX_TEXT
    assert "dependency review entrypoint" in SCRIPTS_INDEX_TEXT
    assert "thin forwarding wrappers" in SCRIPTS_INDEX_TEXT
    assert "health_common.sh" in SCRIPTS_INDEX_TEXT
    assert "built-wheel smoke test" in SCRIPTS_INDEX_TEXT
    assert "fix/verify/package phases" in SCRIPTS_INDEX_TEXT
    assert "review the changes and rerun `doctor.sh`" in SCRIPTS_INDEX_TEXT
    assert "single weekly grouped Dependabot PR for `uv`" in SCRIPTS_INDEX_TEXT


def test_dependabot_configuration_prefers_a_single_grouped_uv_pr() -> None:
    assert 'package-ecosystem: "uv"' in DEPENDABOT_TEXT
    assert 'package-ecosystem: "github-actions"' not in DEPENDABOT_TEXT
    assert "open-pull-requests-limit: 1" in DEPENDABOT_TEXT
    assert "uv-all-updates" in DEPENDABOT_TEXT


def test_conformance_script_is_repository_owned_and_external_tck_independent() -> None:
    assert 'run_shared_repo_health_prerequisites "conformance"' in CONFORMANCE_TEXT
    assert "Run repository-owned A2A 1.0 black-box compatibility probes." in CONFORMANCE_TEXT
    assert "does not download or depend on an external TCK" in CONFORMANCE_TEXT
    assert "scripts.conformance_probe" in CONFORMANCE_TEXT
    assert "scripts.conformance_sut" in CONFORMANCE_TEXT
    assert "a2a-tck" not in CONFORMANCE_TEXT


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


def test_smoke_test_imports_installed_package_before_health_check() -> None:
    assert "find \"${tool_dir}\" \\( -type f -o -type l \\) -path '*/bin/python'" in SMOKE_TEST_TEXT
    assert '"${installed_python}" -c "import opencode_a2a; print(opencode_a2a.__version__)"' in (
        SMOKE_TEST_TEXT
    )


def test_smoke_test_waits_quietly_for_health_and_surfaces_early_exit() -> None:
    assert '"${tool_bin_dir}/opencode-a2a" serve >"${server_log}" 2>&1 &' in SMOKE_TEST_TEXT
    assert "wait_for_health()" in SMOKE_TEST_TEXT
    assert 'if ! kill -0 "${server_pid}" >/dev/null 2>&1; then' in SMOKE_TEST_TEXT
    assert "curl --silent --fail --output /dev/null \\" in SMOKE_TEST_TEXT
    assert '"${health_url}" 2>/dev/null; then' in SMOKE_TEST_TEXT


def test_coverage_policy_tracks_overall_and_critical_file_thresholds() -> None:
    assert "OVERALL_MINIMUM = 90.0" in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/execution/executor.py": 90.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/server/application.py": 90.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/jsonrpc/application.py": 85.0' in COVERAGE_GATE_TEXT
    assert '"src/opencode_a2a/opencode_upstream_client.py": 85.0' in COVERAGE_GATE_TEXT
    assert "--cov-fail-under=90" in PYPROJECT_TEXT
    assert "--cov-report=json:.coverage.json" in PYPROJECT_TEXT


def test_thin_wrapper_finder_keeps_static_analysis_scope() -> None:
    assert "ast.parse" in THIN_WRAPPER_FINDER_TEXT
    assert "--max-callers" in THIN_WRAPPER_FINDER_TEXT
    assert "--thin-only" in THIN_WRAPPER_FINDER_TEXT
    assert "thin_forwarder" in THIN_WRAPPER_FINDER_TEXT
