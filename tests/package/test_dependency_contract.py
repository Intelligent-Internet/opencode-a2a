from importlib.metadata import requires

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


def test_installed_metadata_requests_sqlalchemy_asyncio() -> None:
    requirement = _runtime_requirement("sqlalchemy")
    assert "asyncio" in requirement.extras
