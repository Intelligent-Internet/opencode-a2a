import json
import os
import re
import socket
import subprocess
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

DOCTOR_TEXT = Path("scripts/doctor.sh").read_text()
CONFORMANCE_TEXT = Path("scripts/conformance.sh").read_text()
DEPENDENCY_HEALTH_TEXT = Path("scripts/dependency_health.sh").read_text()
HEALTH_COMMON_TEXT = Path("scripts/health_common.sh").read_text()
HEALTH_COMMON_PATH = Path("scripts/health_common.sh").resolve()
LINT_TEXT = Path("scripts/lint.sh").read_text()
SMOKE_TEST_TEXT = Path("scripts/smoke_test_built_cli.sh").read_text()
RELEASE_TAG_SCRIPT_PATH = Path("scripts/validate_release_tag.sh").resolve()
COVERAGE_GATE_TEXT = Path("scripts/check_coverage.py").read_text()
THIN_WRAPPER_FINDER_TEXT = Path("scripts/find_thin_wrappers.py").read_text()
SCRIPTS_INDEX_TEXT = Path("scripts/README.md").read_text()
PYPROJECT_TEXT = Path("pyproject.toml").read_text()
DEPENDABOT_TEXT = Path(".github/dependabot.yml").read_text()
PRECOMMIT_TEXT = Path(".pre-commit-config.yaml").read_text()
BUILD_REQUIREMENTS_TEXT = Path("build-requirements/requirements.txt").read_text()
WORKFLOW_TEXTS = {
    path: path.read_text() for path in sorted(Path(".github/workflows").glob("*.yml"))
}


def test_shared_repo_health_prerequisites_live_in_common_helper() -> None:
    assert "run_shared_repo_health_prerequisites()" in HEALTH_COMMON_TEXT
    assert "repo_state_fingerprint()" in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] sync locked environment"' in HEALTH_COMMON_TEXT
    assert 'echo "[${label}] verify dependency compatibility"' in HEALTH_COMMON_TEXT
    assert "uv sync --all-extras --frozen" in HEALTH_COMMON_TEXT
    assert "uv pip check" in HEALTH_COMMON_TEXT


def test_repo_state_fingerprint_tracks_tracked_and_untracked_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked-v1")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)

    def fingerprint() -> str:
        return subprocess.check_output(
            [
                "bash",
                "-c",
                'source "$1"; repo_state_fingerprint',
                "_",
                str(HEALTH_COMMON_PATH),
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    initial = fingerprint()
    tracked.write_text("tracked-v2")
    tracked_changed = fingerprint()
    assert tracked_changed != initial

    untracked = tmp_path / "untracked.txt"
    untracked.write_text("untracked-v1")
    untracked_added = fingerprint()
    assert untracked_added != tracked_changed

    untracked.write_text("untracked-v2")
    assert fingerprint() != untracked_added


def test_release_tag_validation_requires_existing_tag_at_checkout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("release")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "release",
        ],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "tag",
            "-a",
            "-m",
            "release",
            "v1.2.3",
        ],
        cwd=tmp_path,
        check=True,
    )

    def validate(tag: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(RELEASE_TAG_SCRIPT_PATH), tag],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )

    assert validate("v1.2.3").returncode == 0
    assert validate("release/v1").returncode == 1
    assert validate("v9.9.9").returncode == 1

    tracked.write_text("next")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "next",
        ],
        cwd=tmp_path,
        check=True,
    )
    mismatch = validate("v1.2.3")
    assert mismatch.returncode == 1
    assert "does not point to the checked-out commit" in mismatch.stderr


def test_doctor_keeps_local_regression_scope() -> None:
    assert 'source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"' in DOCTOR_TEXT
    assert 'run_shared_repo_health_prerequisites "doctor"' in DOCTOR_TEXT
    assert "repo_state_fingerprint" in DOCTOR_TEXT
    assert "doctor_repo_state_fingerprint" not in DOCTOR_TEXT
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
    assert "uv build --no-sources --force-pep517" in DOCTOR_TEXT
    assert "--build-constraints build-requirements/requirements.txt" in DOCTOR_TEXT
    assert "--require-hashes" in DOCTOR_TEXT
    assert "bash ./scripts/smoke_test_built_cli.sh dist/opencode_a2a-*.whl" in DOCTOR_TEXT
    assert "uv pip list --outdated" not in DOCTOR_TEXT
    assert "uv run pip-audit" not in DOCTOR_TEXT


def test_lint_gate_rejects_hook_generated_rewrites() -> None:
    assert 'source "${SCRIPT_DIR}/health_common.sh"' in LINT_TEXT
    assert "repo_state_fingerprint" in LINT_TEXT
    assert "uv run pre-commit run --all-files" in LINT_TEXT
    assert "the lint gate rejects hook-generated edits" in LINT_TEXT


def test_dependency_health_keeps_dependency_review_scope() -> None:
    assert (
        'source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/health_common.sh"'
        in DEPENDENCY_HEALTH_TEXT
    )
    assert 'run_shared_repo_health_prerequisites "dependency-health"' in DEPENDENCY_HEALTH_TEXT
    assert "uv pip list --outdated" in DEPENDENCY_HEALTH_TEXT
    assert "uv run pip-audit" in DEPENDENCY_HEALTH_TEXT
    assert "--disable-pip" in DEPENDENCY_HEALTH_TEXT
    assert "--require-hashes" in DEPENDENCY_HEALTH_TEXT
    assert "--strict" in DEPENDENCY_HEALTH_TEXT
    assert "DEPENDENCY_AUDIT_OUTPUT_DIR" in DEPENDENCY_HEALTH_TEXT
    assert "--format json" in DEPENDENCY_HEALTH_TEXT
    assert 'run_dependency_audit "build" "build-requirements/requirements.txt" "build"' in (
        DEPENDENCY_HEALTH_TEXT
    )
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
    assert "dependabot.yml" in SCRIPTS_INDEX_TEXT


def test_dependabot_configuration_separates_critical_and_routine_updates() -> None:
    assert 'package-ecosystem: "uv"' in DEPENDABOT_TEXT
    assert 'package-ecosystem: "github-actions"' in DEPENDABOT_TEXT
    assert 'package-ecosystem: "pre-commit"' in DEPENDABOT_TEXT
    assert 'package-ecosystem: "pip"' in DEPENDABOT_TEXT
    assert 'directory: "/build-requirements"' in DEPENDABOT_TEXT
    assert DEPENDABOT_TEXT.count("cooldown:") == 4
    assert "semver-major-days: 30" in DEPENDABOT_TEXT
    assert "a2a-sdk:" in DEPENDABOT_TEXT
    assert "runtime-framework:" in DEPENDABOT_TEXT
    assert "tooling-and-transitive:" in DEPENDABOT_TEXT
    assert "build-system:" in DEPENDABOT_TEXT
    assert "uv-all-updates" not in DEPENDABOT_TEXT


def test_workflow_actions_are_pinned_to_immutable_commits() -> None:
    action_reference = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
    references = [
        line.split("uses:", 1)[1].split("#", 1)[0].strip()
        for text in WORKFLOW_TEXTS.values()
        for line in text.splitlines()
        if line.strip().startswith("uses:")
    ]

    assert references
    assert all(action_reference.fullmatch(reference) for reference in references)


def test_pre_commit_hooks_are_pinned_to_immutable_commits() -> None:
    revisions = re.findall(r"^\s+rev:\s+(\S+)", PRECOMMIT_TEXT, flags=re.MULTILINE)

    assert revisions
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions)
    assert PRECOMMIT_TEXT.count("# frozen: v") == len(revisions)


def test_build_dependencies_are_pinned_with_hashes() -> None:
    build_system = tomllib.loads(PYPROJECT_TEXT)["build-system"]["requires"]
    assert "setuptools>=80" in build_system
    assert "setuptools-scm[toml]>=8,<11" in build_system

    pins = {
        requirement.name: requirement
        for line in BUILD_REQUIREMENTS_TEXT.splitlines()
        if (stripped := line.strip())
        and not stripped.startswith(("#", "--hash"))
        and (requirement := Requirement(stripped.removesuffix("\\").strip()))
    }
    assert {
        "packaging",
        "setuptools",
        "setuptools-scm",
        "vcs-versioning",
    } <= pins.keys()

    for raw in build_system:
        build_requirement = Requirement(raw)
        pin = pins[build_requirement.name]
        assert len(pin.specifier) == 1
        assert all(
            spec.operator == "=="
            and build_requirement.specifier.contains(Version(spec.version), prereleases=True)
            for spec in pin.specifier
        )
    assert BUILD_REQUIREMENTS_TEXT.count("--hash=sha256:") >= 2 * len(pins)


def test_release_workflow_separates_privileged_publish_from_audited_build() -> None:
    publish_text = WORKFLOW_TEXTS[Path(".github/workflows/publish.yml")]
    assert "permissions: {}" in publish_text
    assert "build-and-audit:" in publish_text
    assert "needs: build-and-audit" in publish_text
    assert "publish-pypi:" in publish_text
    assert "sync-release:" in publish_text
    assert "needs.publish-pypi.result" in publish_text
    assert "persist-credentials: false" in publish_text
    assert "contents: read" in publish_text
    assert "contents: write" in publish_text
    assert "id-token: write" in publish_text
    assert 'bash ./scripts/validate_release_tag.sh "${RELEASE_TAG}"' in publish_text
    assert "Verify release checksums" in publish_text
    assert "packages-dir: pypi-dist" in publish_text
    assert "cyclonedx-json" in publish_text


def test_dependency_review_runs_for_dependency_manifest_changes() -> None:
    review_text = WORKFLOW_TEXTS[Path(".github/workflows/dependency-review.yml")]
    assert "pull_request:" in review_text
    assert '      - "pyproject.toml"' in review_text
    assert '      - "build-requirements/requirements.txt"' in review_text
    assert '      - "uv.lock"' in review_text
    assert "Upload dependency audit reports" in review_text


def test_conformance_script_is_repository_owned_and_external_tck_independent() -> None:
    assert 'run_shared_repo_health_prerequisites "conformance"' in CONFORMANCE_TEXT
    assert "Run repository-owned A2A 1.0 black-box compatibility probes." in CONFORMANCE_TEXT
    assert "does not download or depend on an external TCK" in CONFORMANCE_TEXT
    assert "scripts.conformance_probe" in CONFORMANCE_TEXT
    assert "scripts.conformance_sut" in CONFORMANCE_TEXT
    assert "a2a-tck" not in CONFORMANCE_TEXT


def test_conformance_entrypoint_runs_repository_owned_probes(tmp_path: Path) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    output_dir = tmp_path / "conformance"
    env = {
        **os.environ,
        "CONFORMANCE_SKIP_REPO_SYNC": "1",
        "CONFORMANCE_SUT_PORT": str(port),
        "CONFORMANCE_OUTPUT_DIR": str(output_dir),
    }

    completed = subprocess.run(
        ["bash", "./scripts/conformance.sh"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((output_dir / "report.json").read_text())
    assert report["scope"] == "repository-owned-a2a-1.0-compatibility-probes"
    assert report["summary"] == {"failed": 0, "passed": 8, "total": 8}


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


def test_ruff_enables_security_rules_for_shipped_code() -> None:
    assert 'select = ["E", "F", "I", "B", "UP", "S"]' in PYPROJECT_TEXT
    assert '"tests/**" = ["S"]' in PYPROJECT_TEXT


def test_thin_wrapper_finder_keeps_static_analysis_scope() -> None:
    assert "ast.parse" in THIN_WRAPPER_FINDER_TEXT
    assert "--max-callers" in THIN_WRAPPER_FINDER_TEXT
    assert "--thin-only" in THIN_WRAPPER_FINDER_TEXT
    assert "thin_forwarder" in THIN_WRAPPER_FINDER_TEXT
