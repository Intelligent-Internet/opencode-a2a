from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from ...profile.runtime import (
    SESSION_SHELL_TOGGLE,
    WORKSPACE_MUTATIONS_TOGGLE,
    RuntimeProfile,
)
from . import catalog, identifiers

OpenCodeApiVersion = Literal["v1", "v2"]

DECLARED_EXTENSION_URIS: tuple[str, ...] = (
    identifiers.SESSION_BINDING_EXTENSION_URI,
    identifiers.MODEL_SELECTION_EXTENSION_URI,
    identifiers.STREAMING_EXTENSION_URI,
    identifiers.SESSION_MANAGEMENT_EXTENSION_URI,
    identifiers.PROVIDER_DISCOVERY_EXTENSION_URI,
    identifiers.WORKSPACE_CONTROL_EXTENSION_URI,
    identifiers.INTERRUPT_RECOVERY_EXTENSION_URI,
    identifiers.INTERRUPT_CALLBACK_EXTENSION_URI,
)

_ALL_EXTENSION_METHODS = frozenset(
    (
        *catalog.SESSION_METHODS.values(),
        *catalog.PROVIDER_DISCOVERY_METHODS.values(),
        *catalog.WORKSPACE_CONTROL_METHODS.values(),
        *catalog.INTERRUPT_RECOVERY_METHODS.values(),
        *catalog.INTERRUPT_CALLBACK_METHODS.values(),
    )
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
    upstream_api_version: OpenCodeApiVersion
    conditional_methods: Mapping[str, DeploymentConditionalMethod]

    @property
    def extensions_enabled(self) -> bool:
        return self.upstream_api_version == "v1"

    @property
    def a2a_message_execution_enabled(self) -> bool:
        return self.upstream_api_version == "v1"

    @property
    def streaming_enabled(self) -> bool:
        return self.upstream_api_version == "v1"

    @property
    def declared_extension_uris(self) -> tuple[str, ...]:
        return DECLARED_EXTENSION_URIS if self.extensions_enabled else ()

    def is_method_enabled(self, method: str) -> bool:
        if method in _ALL_EXTENSION_METHODS and not self.extensions_enabled:
            return False
        conditional_method = self.conditional_methods.get(method)
        if conditional_method is None:
            return True
        return conditional_method.enabled

    def session_management_methods(self) -> dict[str, str]:
        if not self.extensions_enabled:
            return {}
        methods = dict(catalog.SESSION_METHODS)
        if not self.is_method_enabled(catalog.SESSION_METHODS["shell"]):
            methods.pop("shell", None)
        return methods

    def dispatch_method_bindings(self) -> dict[str, str]:
        """Return handler registry bindings; every call is still checked against this policy."""
        methods = self.session_management_methods()
        if not self.extensions_enabled:
            methods = dict(catalog.SESSION_METHODS)
            if not self._deployment_method_enabled(catalog.SESSION_METHODS["shell"]):
                methods.pop("shell", None)
        methods.update(catalog.PROVIDER_DISCOVERY_METHODS)
        methods.update(self.dispatch_workspace_control_methods())
        methods.update(catalog.INTERRUPT_RECOVERY_METHODS)
        methods.update(catalog.INTERRUPT_CALLBACK_METHODS)
        return methods

    def _deployment_method_enabled(self, method: str) -> bool:
        conditional_method = self.conditional_methods.get(method)
        return conditional_method is None or conditional_method.enabled

    def dispatch_workspace_control_methods(self) -> dict[str, str]:
        methods = dict(catalog.WORKSPACE_DISCOVERY_METHODS)
        for key, method in catalog.WORKSPACE_MUTATION_METHODS.items():
            if self._deployment_method_enabled(method):
                methods[key] = method
        return methods

    def session_control_methods(self) -> dict[str, str]:
        if not self.extensions_enabled:
            return {}
        methods = dict(catalog.SESSION_CONTROL_METHODS)
        if not self.is_method_enabled(catalog.SESSION_CONTROL_METHODS["shell"]):
            methods.pop("shell", None)
        return methods

    def workspace_control_methods(self) -> dict[str, str]:
        if not self.extensions_enabled:
            return {}
        return self.dispatch_workspace_control_methods()

    def supported_jsonrpc_methods(self) -> list[str]:
        methods = list(catalog.CORE_JSONRPC_METHODS)
        if not self.extensions_enabled:
            return methods
        methods.extend(method for key, method in catalog.SESSION_METHODS.items() if key != "shell")
        methods.extend(catalog.PROVIDER_DISCOVERY_METHODS.values())
        methods.extend(self.workspace_control_methods().values())
        methods.extend(catalog.INTERRUPT_RECOVERY_METHODS.values())
        methods.extend(catalog.INTERRUPT_CALLBACK_METHODS.values())
        if self.is_method_enabled(catalog.SESSION_CONTROL_METHODS["shell"]):
            methods.append(catalog.SESSION_CONTROL_METHODS["shell"])
        return methods

    def extension_jsonrpc_methods(self) -> list[str]:
        if not self.extensions_enabled:
            return []
        methods = [
            *(method for key, method in catalog.SESSION_METHODS.items() if key != "shell"),
            *catalog.PROVIDER_DISCOVERY_METHODS.values(),
            *self.workspace_control_methods().values(),
            *catalog.INTERRUPT_RECOVERY_METHODS.values(),
            *catalog.INTERRUPT_CALLBACK_METHODS.values(),
        ]
        if self.is_method_enabled(catalog.SESSION_CONTROL_METHODS["shell"]):
            methods.append(catalog.SESSION_CONTROL_METHODS["shell"])
        return methods

    def conditionally_available_methods(self) -> dict[str, dict[str, str]]:
        if not self.extensions_enabled:
            return {}
        return {
            method: disabled_entry
            for method, conditional_method in self.conditional_methods.items()
            if (disabled_entry := conditional_method.disabled_wire_contract_entry()) is not None
        }

    def method_flags(self, methods: Collection[str]) -> dict[str, dict[str, Any]]:
        if not self.extensions_enabled:
            return {}
        return {
            method: conditional_method.control_method_flag()
            for method, conditional_method in self.conditional_methods.items()
            if method in methods
        }

    def conditional_method_retention(self) -> dict[str, dict[str, Any]]:
        if not self.extensions_enabled:
            return {}
        return {
            method: conditional_method.method_retention()
            for method, conditional_method in self.conditional_methods.items()
        }


def build_capability_snapshot(
    *,
    runtime_profile: RuntimeProfile,
    upstream_api_version: OpenCodeApiVersion = "v1",
) -> JsonRpcCapabilitySnapshot:
    conditional_methods = {
        catalog.SESSION_CONTROL_METHODS["shell"]: DeploymentConditionalMethod(
            method=catalog.SESSION_CONTROL_METHODS["shell"],
            enabled=runtime_profile.session_shell.enabled,
            extension_uri=identifiers.SESSION_MANAGEMENT_EXTENSION_URI,
            toggle=SESSION_SHELL_TOGGLE,
        )
    }
    conditional_methods.update(
        {
            method: DeploymentConditionalMethod(
                method=method,
                enabled=runtime_profile.workspace_mutations.enabled,
                extension_uri=identifiers.WORKSPACE_CONTROL_EXTENSION_URI,
                toggle=WORKSPACE_MUTATIONS_TOGGLE,
            )
            for method in catalog.WORKSPACE_MUTATION_METHODS.values()
        }
    )
    return JsonRpcCapabilitySnapshot(
        upstream_api_version=upstream_api_version,
        conditional_methods=MappingProxyType(conditional_methods),
    )
