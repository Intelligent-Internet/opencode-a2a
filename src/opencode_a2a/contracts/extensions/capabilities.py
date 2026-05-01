from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from ...profile.runtime import (
    SESSION_SHELL_TOGGLE,
    WORKSPACE_MUTATIONS_TOGGLE,
    RuntimeProfile,
)
from .catalog import (
    CORE_JSONRPC_METHODS,
    INTERRUPT_CALLBACK_METHODS,
    INTERRUPT_RECOVERY_METHODS,
    PROVIDER_DISCOVERY_METHODS,
    SESSION_CONTROL_METHODS,
    SESSION_METHODS,
    WORKSPACE_DISCOVERY_METHODS,
    WORKSPACE_MUTATION_METHODS,
)
from .identifiers import (
    SESSION_MANAGEMENT_EXTENSION_URI,
    WORKSPACE_CONTROL_EXTENSION_URI,
)


@dataclass(frozen=True)
class DeploymentConditionalMethod:
    method: str
    enabled: bool
    extension_uri: str
    toggle: str
    reason_when_disabled: str = "disabled_by_configuration"

    def control_method_flag(self) -> dict[str, Any]:
        return {
            "enabled_by_default": False,
            "config_key": self.toggle,
        }

    def method_retention(self) -> dict[str, Any]:
        return {
            "surface": "extension",
            "availability": "enabled" if self.enabled else "disabled",
            "retention": "deployment-conditional",
            "extension_uri": self.extension_uri,
            "toggle": self.toggle,
        }

    def disabled_wire_contract_entry(self) -> dict[str, str] | None:
        if self.enabled:
            return None
        return {
            "reason": self.reason_when_disabled,
            "toggle": self.toggle,
        }


@dataclass(frozen=True)
class JsonRpcCapabilitySnapshot:
    conditional_methods: dict[str, DeploymentConditionalMethod]

    def is_method_enabled(self, method: str) -> bool:
        conditional_method = self.conditional_methods.get(method)
        if conditional_method is None:
            return True
        return conditional_method.enabled

    def session_management_methods(self) -> dict[str, str]:
        methods = dict(SESSION_METHODS)
        if not self.is_method_enabled(SESSION_METHODS["shell"]):
            methods.pop("shell", None)
        return methods

    def session_control_methods(self) -> dict[str, str]:
        methods = dict(SESSION_CONTROL_METHODS)
        if not self.is_method_enabled(SESSION_CONTROL_METHODS["shell"]):
            methods.pop("shell", None)
        return methods

    def workspace_control_methods(self) -> dict[str, str]:
        methods = dict(WORKSPACE_DISCOVERY_METHODS)
        for key, method in WORKSPACE_MUTATION_METHODS.items():
            if self.is_method_enabled(method):
                methods[key] = method
        return methods

    def supported_jsonrpc_methods(self) -> list[str]:
        methods = [
            *CORE_JSONRPC_METHODS,
            *(method for key, method in SESSION_METHODS.items() if key != "shell"),
            *PROVIDER_DISCOVERY_METHODS.values(),
            *self.workspace_control_methods().values(),
            *INTERRUPT_RECOVERY_METHODS.values(),
            *INTERRUPT_CALLBACK_METHODS.values(),
        ]
        if self.is_method_enabled(SESSION_CONTROL_METHODS["shell"]):
            methods.append(SESSION_CONTROL_METHODS["shell"])
        return methods

    def extension_jsonrpc_methods(self) -> list[str]:
        methods = [
            *(method for key, method in SESSION_METHODS.items() if key != "shell"),
            *PROVIDER_DISCOVERY_METHODS.values(),
            *self.workspace_control_methods().values(),
            *INTERRUPT_RECOVERY_METHODS.values(),
            *INTERRUPT_CALLBACK_METHODS.values(),
        ]
        if self.is_method_enabled(SESSION_CONTROL_METHODS["shell"]):
            methods.append(SESSION_CONTROL_METHODS["shell"])
        return methods

    def conditionally_available_methods(self) -> dict[str, dict[str, str]]:
        return {
            method: disabled_entry
            for method, conditional_method in self.conditional_methods.items()
            if (disabled_entry := conditional_method.disabled_wire_contract_entry()) is not None
        }

    def method_flags(self, methods: Collection[str]) -> dict[str, dict[str, Any]]:
        return {
            method: conditional_method.control_method_flag()
            for method, conditional_method in self.conditional_methods.items()
            if method in methods
        }

    def conditional_method_retention(self) -> dict[str, dict[str, Any]]:
        return {
            method: conditional_method.method_retention()
            for method, conditional_method in self.conditional_methods.items()
        }


def build_capability_snapshot(*, runtime_profile: RuntimeProfile) -> JsonRpcCapabilitySnapshot:
    conditional_methods = {
        SESSION_CONTROL_METHODS["shell"]: DeploymentConditionalMethod(
            method=SESSION_CONTROL_METHODS["shell"],
            enabled=runtime_profile.session_shell.enabled,
            extension_uri=SESSION_MANAGEMENT_EXTENSION_URI,
            toggle=SESSION_SHELL_TOGGLE,
        )
    }
    conditional_methods.update(
        {
            method: DeploymentConditionalMethod(
                method=method,
                enabled=runtime_profile.workspace_mutations.enabled,
                extension_uri=WORKSPACE_CONTROL_EXTENSION_URI,
                toggle=WORKSPACE_MUTATIONS_TOGGLE,
            )
            for method in WORKSPACE_MUTATION_METHODS.values()
        }
    )
    return JsonRpcCapabilitySnapshot(conditional_methods=conditional_methods)
