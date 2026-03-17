from pathlib import Path

DEPLOY_SH_TEXT = Path("scripts/deploy.sh").read_text()
SETUP_INSTANCE_TEXT = Path("scripts/deploy/setup_instance.sh").read_text()
INSTALL_UNITS_TEXT = Path("scripts/deploy/install_units.sh").read_text()
README_TEXT = Path("README.md").read_text()
SECURITY_TEXT = Path("SECURITY.md").read_text()
DEPLOY_README_TEXT = Path("scripts/deploy_readme.md").read_text()
DEPLOY_RELEASE_README_TEXT = Path("scripts/deploy_release_readme.md").read_text()
SCRIPTS_INDEX_TEXT = Path("scripts/README.md").read_text()
AGENT_DEPLOY_SOP_TEXT = Path("docs/agent_deploy_sop.md").read_text()


def test_deploy_defaults_to_operator_provisioned_runtime_secrets() -> None:
    expected_default = 'export ENABLE_SECRET_PERSISTENCE="${ENABLE_SECRET_PERSISTENCE:-false}"'
    assert expected_default in DEPLOY_SH_TEXT
    assert "enable_secret_persistence)" in DEPLOY_SH_TEXT
    assert 'if [[ -z "$PROJECT_NAME" ]]; then' in DEPLOY_SH_TEXT
    assert "GH_TOKEN=<token> A2A_BEARER_TOKEN=<token>" not in DEPLOY_SH_TEXT


def test_systemd_units_split_secret_and_non_secret_env_files() -> None:
    assert "EnvironmentFile=${DATA_ROOT}/%i/config/opencode.env" in INSTALL_UNITS_TEXT
    assert "EnvironmentFile=-${DATA_ROOT}/%i/config/opencode.auth.env" in INSTALL_UNITS_TEXT
    assert "EnvironmentFile=-${DATA_ROOT}/%i/config/opencode.secret.env" in INSTALL_UNITS_TEXT
    assert "EnvironmentFile=${DATA_ROOT}/%i/config/a2a.env" in INSTALL_UNITS_TEXT
    assert "EnvironmentFile=-${DATA_ROOT}/%i/config/a2a.secret.env" in INSTALL_UNITS_TEXT


def test_setup_instance_generates_examples_and_requires_runtime_secret_files() -> None:
    required_a2a_secret = 'require_runtime_secret_file "$A2A_SECRET_ENV_FILE" "A2A_BEARER_TOKEN"'
    secret_persistence_notice = (
        "deploy will not write GH_TOKEN, A2A_BEARER_TOKEN, or provider keys to disk"
    )
    assert ': "${ENABLE_SECRET_PERSISTENCE:=false}"' in SETUP_INSTANCE_TEXT
    assert "opencode.auth.env.example" in SETUP_INSTANCE_TEXT
    assert "a2a.secret.env.example" in SETUP_INSTANCE_TEXT
    assert "opencode.secret.env.example" in SETUP_INSTANCE_TEXT
    assert 'require_runtime_secret_file "$OPENCODE_AUTH_ENV_FILE" "GH_TOKEN"' in SETUP_INSTANCE_TEXT
    assert required_a2a_secret in SETUP_INSTANCE_TEXT
    assert secret_persistence_notice in SETUP_INSTANCE_TEXT
    assert "Value for ${key} contains a newline or carriage return" in SETUP_INSTANCE_TEXT
    assert ': "${A2A_MAX_REQUEST_BODY_BYTES:=1048576}"' in SETUP_INSTANCE_TEXT
    assert ': "${A2A_STRICT_ISOLATION:=false}"' in SETUP_INSTANCE_TEXT
    assert "data_root_supports_protect_home()" in SETUP_INSTANCE_TEXT
    assert "/home|/root|/run/user|/home/*|/root/*|/run/user/*" in SETUP_INSTANCE_TEXT
    assert "TemporaryFileSystem=${DATA_ROOT}:ro" in SETUP_INSTANCE_TEXT
    assert "BindPaths=${PROJECT_DIR}:${PROJECT_DIR}" in SETUP_INSTANCE_TEXT
    request_limit_line = (
        'append_env_line "$a2a_env_tmp" "A2A_MAX_REQUEST_BODY_BYTES" '
        '"${A2A_MAX_REQUEST_BODY_BYTES}"'
    )
    assert request_limit_line in SETUP_INSTANCE_TEXT


def test_deploy_supports_release_and_source_install_modes() -> None:
    assert 'export A2A_INSTALL_MODE="${A2A_INSTALL_MODE:-source}"' in DEPLOY_SH_TEXT
    assert "A2A_INSTALL_MODE must be source or release" in DEPLOY_SH_TEXT
    assert 'if [[ "$A2A_INSTALL_MODE" == "release" ]]; then' in DEPLOY_SH_TEXT
    assert '"${SCRIPT_DIR}/deploy/install_release_runtime.sh"' in DEPLOY_SH_TEXT
    assert 'export FORCE_A2A_RELEASE_INSTALL="true"' in DEPLOY_SH_TEXT
    assert "Environment=A2A_BIN=${A2A_BIN}" in INSTALL_UNITS_TEXT
    assert "ExecStart=${DEPLOY_HELPER_DIR}/run_a2a.sh" in INSTALL_UNITS_TEXT
    assert "ExecStart=${DEPLOY_HELPER_DIR}/run_opencode.sh" in INSTALL_UNITS_TEXT


def test_security_docs_emphasize_single_tenant_boundary_and_secret_strategy() -> None:
    assert "single-tenant trust boundary" in README_TEXT
    assert "a2a-client-hub" in README_TEXT
    assert "```mermaid" in README_TEXT
    assert "[SECURITY.md](SECURITY.md)" in README_TEXT
    assert "uv tool install opencode-a2a-server" in README_TEXT
    assert "Install Released CLI" in README_TEXT
    assert "scripts/deploy_release.sh" in README_TEXT
    assert "secret persistence is opt-in" in SECURITY_TEXT
    assert "single-tenant trust boundary" in SECURITY_TEXT
    assert "ENABLE_SECRET_PERSISTENCE=false" in DEPLOY_README_TEXT
    assert "opencode.auth.env" in DEPLOY_README_TEXT
    assert "a2a.secret.env" in DEPLOY_README_TEXT
    assert "release-based systemd deployment" in DEPLOY_RELEASE_README_TEXT
    assert "source-based multi-instance systemd deployment" in SCRIPTS_INDEX_TEXT
    assert "lightweight local foreground launcher" in SCRIPTS_INDEX_TEXT
    assert "release-based, systemd-managed, recommended" in AGENT_DEPLOY_SOP_TEXT
    assert "deploy_light.sh start" not in AGENT_DEPLOY_SOP_TEXT


def test_uninstall_removes_instance_systemd_overrides() -> None:
    uninstall_text = Path("scripts/uninstall.sh").read_text()
    opencode_override_dir = (
        'OPENCODE_OVERRIDE_DIR="/etc/systemd/system/opencode@${PROJECT_NAME}.service.d"'
    )
    remove_override_cmd = (
        'run_ignore sudo rm -rf -- "${A2A_OVERRIDE_DIR}" "${OPENCODE_OVERRIDE_DIR}"'
    )
    assert opencode_override_dir in uninstall_text
    assert (
        'A2A_OVERRIDE_DIR="/etc/systemd/system/opencode-a2a-server@${PROJECT_NAME}.service.d"'
        in uninstall_text
    )
    assert remove_override_cmd in uninstall_text
    assert "run_ignore sudo systemctl daemon-reload" in uninstall_text
