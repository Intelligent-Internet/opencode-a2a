from importlib.metadata import requires

import pytest
from packaging.requirements import Requirement


def _runtime_requirement(name: str) -> Requirement:
    requirements = [
        requirement
        for raw in requires("opencode-a2a") or []
        if (requirement := Requirement(raw)).name == name
    ]
    assert len(requirements) == 1, f"Expected one requirement for {name}"
    assert requirements[0].marker is None, f"{name} must be required on every installation"
    return requirements[0]


@pytest.mark.parametrize(
    ("name", "unsafe_version", "safe_version"),
    [("click", "8.3.2", "8.3.3"), ("pyasn1", "0.6.3", "0.6.4"), ("urllib3", "2.7.0", "2.8.0")],
)
def test_installed_metadata_enforces_security_floors(
    name: str, unsafe_version: str, safe_version: str
) -> None:
    requirement = _runtime_requirement(name)
    assert unsafe_version not in requirement.specifier
    assert safe_version in requirement.specifier


def test_installed_metadata_requests_sqlalchemy_asyncio() -> None:
    requirement = _runtime_requirement("sqlalchemy")
    assert "asyncio" in requirement.extras
    assert "2.0.54" in requirement.specifier
    assert "2.1.1" in requirement.specifier
    assert "3.0.0" not in requirement.specifier


def test_installed_metadata_bounds_supported_protobuf_versions() -> None:
    requirement = _runtime_requirement("protobuf")
    assert "6.33.4" not in requirement.specifier
    assert "6.33.5" in requirement.specifier
    assert "7.36.2" in requirement.specifier
    assert "8.0.0" not in requirement.specifier
