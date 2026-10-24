from importlib.metadata import requires

from packaging.requirements import Requirement
from packaging.version import Version


def _runtime_requirement(name: str) -> Requirement:
    requirements = [
        requirement
        for raw in requires("opencode-a2a") or []
        if (requirement := Requirement(raw)).name == name and requirement.marker is None
    ]
    assert len(requirements) == 1, f"Expected one requirement for {name}"
    return requirements[0]


def test_installed_metadata_requests_sqlalchemy_asyncio() -> None:
    requirement = _runtime_requirement("sqlalchemy")
    assert "asyncio" in requirement.extras


def test_installed_metadata_requests_certifi_security_floor() -> None:
    requirement = _runtime_requirement("certifi")
    assert Version("2026.10.19") in requirement.specifier


def test_installed_metadata_pins_reviewed_a2a_sdk() -> None:
    requirement = _runtime_requirement("a2a-sdk")
    assert str(requirement.specifier) == "==1.2.3"
