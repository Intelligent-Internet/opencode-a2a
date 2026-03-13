from pathlib import Path

DEPLOY_SH_TEXT = Path("scripts/deploy.sh").read_text()
SETUP_INSTANCE_TEXT = Path("scripts/deploy/setup_instance.sh").read_text()
INSTALL_UNITS_TEXT = Path("scripts/deploy/install_units.sh").read_text()
README_TEXT = Path("README.md").read_text()
SECURITY_TEXT = Path("SECURITY.md").read_text()
DEPLOY_README_TEXT = Path("scripts/deploy_readme.md").read_text()


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


def test_security_docs_emphasize_single_tenant_boundary_and_secret_strategy() -> None:
    assert "single-tenant trust boundary" in README_TEXT
    assert "a2a-client-hub" in README_TEXT
    assert "```mermaid" in README_TEXT
    assert "[SECURITY.md](SECURITY.md)" in README_TEXT
    assert "secret persistence is opt-in" in SECURITY_TEXT
    assert "single-tenant trust boundary" in SECURITY_TEXT
    assert "ENABLE_SECRET_PERSISTENCE=false" in DEPLOY_README_TEXT
    assert "opencode.auth.env" in DEPLOY_README_TEXT
    assert "a2a.secret.env" in DEPLOY_README_TEXT
