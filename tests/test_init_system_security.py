from pathlib import Path
import re


INIT_SYSTEM_PATH = Path("scripts/init_system.sh")
INIT_SYSTEM_TEXT = INIT_SYSTEM_PATH.read_text()


def _extract_var(var_name: str) -> str:
    match = re.search(rf"^{var_name}=\"([^\"]*)\"", INIT_SYSTEM_TEXT, re.MULTILINE)
    if not match:
        msg = f"Missing constant {var_name} in scripts/init_system.sh"
        raise AssertionError(msg)
    return match.group(1)


def test_opencode_install_flow_is_pinned_and_verified() -> None:
    url = _extract_var("OPENCODE_INSTALLER_URL")
    version = _extract_var("OPENCODE_INSTALLER_VERSION")
    checksum = _extract_var("OPENCODE_INSTALLER_SHA256")
    install_cmd = _extract_var("OPENCODE_INSTALL_CMD")

    assert url == "https://opencode.ai/install"
    assert version
    assert re.fullmatch(r"[0-9a-zA-Z._-]+", version)
    assert re.fullmatch(r"[0-9a-f]{64}", checksum)
    assert "bash -" not in install_cmd
    assert "--version" in install_cmd
    assert "curl -fsSL https://opencode.ai/install | bash" not in INIT_SYSTEM_TEXT
    assert "download_script \"$OPENCODE_INSTALLER_URL\"" in INIT_SYSTEM_TEXT
    assert "verify_file_checksum \"$opencode_install_script\" \"$OPENCODE_INSTALLER_SHA256\"" in INIT_SYSTEM_TEXT


def _parse_octal_mode(mode: str) -> int:
    return int(mode, 8)


def test_uv_python_default_permissions_are_not_world_writable() -> None:
    initial_mode = _extract_var("UV_PYTHON_DIR_MODE")
    final_mode = _extract_var("UV_PYTHON_DIR_FINAL_MODE")
    mode_groups = _parse_octal_mode(initial_mode), _parse_octal_mode(final_mode)
    for mode in mode_groups:
        assert mode & 0o002 == 0
    assert initial_mode != "777"
    assert final_mode != "777"
