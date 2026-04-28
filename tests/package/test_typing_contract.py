from pathlib import Path

PYPROJECT_TEXT = Path("pyproject.toml").read_text()


def test_package_declares_py_typed_marker() -> None:
    assert Path("src/opencode_a2a/py.typed").is_file()
    assert "[tool.setuptools.package-data]" in PYPROJECT_TEXT
    assert 'opencode_a2a = ["py.typed"]' in PYPROJECT_TEXT


def test_package_declares_runtime_protobuf_dependency() -> None:
    assert '"protobuf>=6.33.5,<7.0"' in PYPROJECT_TEXT


def test_build_backend_pins_setuptools_scm_below_warninging_major_version() -> None:
    assert 'requires = ["setuptools>=80", "setuptools-scm[toml]>=8,<10"]' in PYPROJECT_TEXT
