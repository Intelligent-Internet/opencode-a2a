import re
from pathlib import Path

INIT_SYSTEM_PATH = Path("scripts/init_system.sh")
INIT_SYSTEM_TEXT = INIT_SYSTEM_PATH.read_text()
UV_MANIFEST_PATH = Path("scripts/init_system_uv_release_manifest.sh")
UV_MANIFEST_TEXT = UV_MANIFEST_PATH.read_text()


def _extract_var(text: str, var_name: str) -> str:
    match = re.search(rf"^{var_name}=\"([^\"]*)\"", text, re.MULTILINE)
    if not match:
        msg = f"Missing constant {var_name}"
        raise AssertionError(msg)
    return match.group(1)


def test_opencode_install_flow_is_pinned_and_verified() -> None:
    url = _extract_var(INIT_SYSTEM_TEXT, "OPENCODE_INSTALLER_URL")
    version = _extract_var(INIT_SYSTEM_TEXT, "OPENCODE_INSTALLER_VERSION")
    checksum = _extract_var(INIT_SYSTEM_TEXT, "OPENCODE_INSTALLER_SHA256")
    install_cmd = _extract_var(INIT_SYSTEM_TEXT, "OPENCODE_INSTALL_CMD")

    assert url == "https://opencode.ai/install"
    assert version
    assert re.fullmatch(r"[0-9a-zA-Z._-]+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", checksum)
    assert "bash -" not in install_cmd
    assert "--version" in install_cmd
    assert "curl -fsSL https://opencode.ai/install | bash" not in INIT_SYSTEM_TEXT
    assert 'source "${SCRIPT_DIR}/init_system_uv_release_manifest.sh"' in INIT_SYSTEM_TEXT
    assert 'download_file "$OPENCODE_INSTALLER_URL"' in INIT_SYSTEM_TEXT
    assert (
        'verify_file_checksum "$opencode_install_script" "$OPENCODE_INSTALLER_SHA256"'
        in INIT_SYSTEM_TEXT
    )


def test_uv_install_flow_is_pinned_to_release_tarballs() -> None:
    version = _extract_var(UV_MANIFEST_TEXT, "UV_VERSION")
    base_url = _extract_var(UV_MANIFEST_TEXT, "UV_RELEASE_BASE_URL")

    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
    assert base_url == "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}"
    assert "astral.sh/uv/install.sh" not in INIT_SYSTEM_TEXT
    assert "install_uv_from_release" in INIT_SYSTEM_TEXT
    assert "resolve_uv_release_artifact" in INIT_SYSTEM_TEXT
    for checksum_var in (
        "UV_TARBALL_X86_64_GNU_SHA256",
        "UV_TARBALL_X86_64_MUSL_SHA256",
        "UV_TARBALL_AARCH64_GNU_SHA256",
        "UV_TARBALL_AARCH64_MUSL_SHA256",
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", _extract_var(UV_MANIFEST_TEXT, checksum_var))


def test_node_install_flow_avoids_remote_setup_scripts() -> None:
    assert "deb.nodesource.com/setup_" not in INIT_SYSTEM_TEXT
    assert "rpm.nodesource.com/setup_" not in INIT_SYSTEM_TEXT
    assert "NodeSource setup script" not in INIT_SYSTEM_TEXT
    assert "Installing Node.js from trusted package manager repositories" in INIT_SYSTEM_TEXT
    assert "Install Node.js >= ${NODE_MAJOR} manually" in INIT_SYSTEM_TEXT


def test_repo_bootstrap_defaults_to_main_ref() -> None:
    assert _extract_var(INIT_SYSTEM_TEXT, "OPENCODE_A2A_REF") == "main"
    assert _extract_var(INIT_SYSTEM_TEXT, "INSTALL_A2A_SOURCE") == "true"
    assert "OPENCODE_A2A_REPO_READY=0" in INIT_SYSTEM_TEXT
    assert 'if [[ "$OPENCODE_A2A_REPO_READY" -ne 1 ]]; then' in INIT_SYSTEM_TEXT
    assert "Repository ref is not ready; skipping A2A venv creation." in INIT_SYSTEM_TEXT
    assert "resolve_opencode_a2a_ref" in INIT_SYSTEM_TEXT
    assert "fetch_github_latest_release_tag" in INIT_SYSTEM_TEXT
    assert (
        "source-based OpenCode + A2A deployment"
        in Path("scripts/init_system_readme.md").read_text()
    )
    assert "init_release_system.sh" in Path("scripts/init_system_readme.md").read_text()


def test_release_bootstrap_wrapper_disables_source_checkout() -> None:
    release_init_text = Path("scripts/init_release_system.sh").read_text()
    release_init_readme_text = Path("scripts/init_release_system_readme.md").read_text()

    assert 'export INSTALL_A2A_SOURCE="false"' in release_init_text
    assert '"${SCRIPT_DIR}/deploy/install_release_runtime.sh"' in release_init_text
    assert "does not clone the" in release_init_readme_text
    assert "/opt/opencode-a2a-release" in release_init_readme_text


def _parse_octal_mode(mode: str) -> int:
    return int(mode, 8)


def test_uv_python_default_permissions_are_not_world_writable() -> None:
    initial_mode = _extract_var(INIT_SYSTEM_TEXT, "UV_PYTHON_DIR_MODE")
    final_mode = _extract_var(INIT_SYSTEM_TEXT, "UV_PYTHON_DIR_FINAL_MODE")
    mode_groups = _parse_octal_mode(initial_mode), _parse_octal_mode(final_mode)
    for mode in mode_groups:
        assert mode & 0o002 == 0
    assert initial_mode != "777"
    assert final_mode != "777"
