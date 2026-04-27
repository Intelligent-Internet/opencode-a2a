from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Generator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine


def _install_legacy_test_shims() -> None:
    import a2a.types as legacy_types
    from a2a.client import errors as client_errors
    from a2a.server.request_handlers.default_request_handler import LegacyRequestHandler
    from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
    from a2a.server.routes.rest_dispatcher import RestDispatcher
    from a2a.types import GetTaskRequest, SendMessageConfiguration, SendMessageRequest
    from a2a.utils import errors as protocol_errors
    from a2a.utils.constants import TransportProtocol

    from opencode_a2a.jsonrpc.models import JSONRPCError, JSONRPCErrorResponse

    class A2AClientHTTPError(client_errors.A2AClientError):
        def __init__(self, status_code: int, message: str):
            self.status_code = status_code
            super().__init__(f"HTTP Error {status_code}: {message}")

    class A2AClientJSONError(client_errors.A2AClientError):
        pass

    class A2AClientJSONRPCError(client_errors.A2AClientError):
        def __init__(self, response: object):
            self.response = response
            error = getattr(response, "error", None)
            message = getattr(error, "message", "JSON-RPC error")
            super().__init__(message)

    class MessageSendConfiguration:
        def __new__(cls, *args, **kwargs):
            del args
            accepted_output_modes = kwargs.pop("acceptedOutputModes", None)
            if accepted_output_modes is not None:
                kwargs["accepted_output_modes"] = accepted_output_modes
            return SendMessageConfiguration(**kwargs)

    @dataclass
    class TextPart:
        text: str

    @dataclass
    class DataPart:
        data: object

    @dataclass
    class FileWithBytes:
        bytes: str
        mimeType: str | None = None
        name: str | None = None

    @dataclass
    class FileWithUri:
        uri: str
        mimeType: str | None = None
        name: str | None = None

    @dataclass
    class FilePart:
        file: FileWithBytes | FileWithUri

    class ServerError(Exception):
        def __init__(self, error: Exception):
            self.error = error
            super().__init__(str(error))

    class RESTAdapter:
        def __init__(self, *, agent_card, http_handler, context_builder=None):
            del agent_card
            self._dispatcher = RestDispatcher(
                request_handler=http_handler,
                context_builder=context_builder,
            )

        def routes(self) -> dict[tuple[str, str], object]:
            return {
                ("/v1/message:send", "POST"): self._dispatcher.on_message_send,
                ("/v1/message:stream", "POST"): self._dispatcher.on_message_send_stream,
                ("/v1/tasks/{id}:cancel", "POST"): self._dispatcher.on_cancel_task,
                ("/v1/tasks/{id}:subscribe", "GET"): self._dispatcher.on_subscribe_to_task,
                ("/v1/tasks/{id}:subscribe", "POST"): self._dispatcher.on_subscribe_to_task,
                ("/v1/tasks/{id}", "GET"): self._dispatcher.on_get_task,
                (
                    "/v1/tasks/{id}/pushNotificationConfigs/{push_id}",
                    "GET",
                ): self._dispatcher.get_push_notification,
                (
                    "/v1/tasks/{id}/pushNotificationConfigs/{push_id}",
                    "DELETE",
                ): self._dispatcher.delete_push_notification,
                (
                    "/v1/tasks/{id}/pushNotificationConfigs",
                    "POST",
                ): self._dispatcher.set_push_notification,
                (
                    "/v1/tasks/{id}/pushNotificationConfigs",
                    "GET",
                ): self._dispatcher.list_push_notifications,
                ("/agent/authenticatedExtendedCard", "GET"): (
                    self._dispatcher.handle_authenticated_agent_card
                ),
            }

    client_errors.A2AClientHTTPError = A2AClientHTTPError
    client_errors.A2AClientJSONError = A2AClientJSONError
    client_errors.A2AClientJSONRPCError = A2AClientJSONRPCError

    legacy_types.A2AError = protocol_errors.A2AError
    legacy_types.InvalidParamsError = protocol_errors.InvalidParamsError
    legacy_types.UnsupportedOperationError = protocol_errors.UnsupportedOperationError
    legacy_types.JSONRPCError = JSONRPCError
    legacy_types.JSONRPCErrorResponse = JSONRPCErrorResponse
    legacy_types.MessageSendConfiguration = MessageSendConfiguration
    legacy_types.MessageSendParams = SendMessageRequest
    legacy_types.TaskIdParams = GetTaskRequest
    legacy_types.TaskQueryParams = GetTaskRequest
    legacy_types.TextPart = TextPart
    legacy_types.DataPart = DataPart
    legacy_types.FilePart = FilePart
    legacy_types.FileWithBytes = FileWithBytes
    legacy_types.FileWithUri = FileWithUri
    legacy_types.TransportProtocol = TransportProtocol

    protocol_errors.ServerError = ServerError

    apps_module = types.ModuleType("a2a.server.apps")
    jsonrpc_module = types.ModuleType("a2a.server.apps.jsonrpc")
    fastapi_app_module = types.ModuleType("a2a.server.apps.jsonrpc.fastapi_app")
    jsonrpc_app_module = types.ModuleType("a2a.server.apps.jsonrpc.jsonrpc_app")
    rest_module = types.ModuleType("a2a.server.apps.rest")
    rest_adapter_module = types.ModuleType("a2a.server.apps.rest.rest_adapter")

    fastapi_app_module.A2AFastAPIApplication = JsonRpcDispatcher
    fastapi_app_module.A2AFastAPI = JsonRpcDispatcher
    jsonrpc_app_module.JSONRPCApplication = JsonRpcDispatcher
    jsonrpc_app_module.DefaultCallContextBuilder = object
    rest_adapter_module.RESTAdapter = RESTAdapter

    sys.modules.setdefault("a2a.server.apps", apps_module)
    sys.modules.setdefault("a2a.server.apps.jsonrpc", jsonrpc_module)
    sys.modules.setdefault("a2a.server.apps.jsonrpc.fastapi_app", fastapi_app_module)
    sys.modules.setdefault("a2a.server.apps.jsonrpc.jsonrpc_app", jsonrpc_app_module)
    sys.modules.setdefault("a2a.server.apps.rest", rest_module)
    sys.modules.setdefault("a2a.server.apps.rest.rest_adapter", rest_adapter_module)

    default_handler_module = sys.modules.get("a2a.server.request_handlers.default_request_handler")
    if default_handler_module is not None and not hasattr(
        default_handler_module, "DefaultRequestHandler"
    ):
        default_handler_module.DefaultRequestHandler = LegacyRequestHandler


_install_legacy_test_shims()


@pytest.fixture(autouse=True)
def dispose_app_database_engines(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    import opencode_a2a.server.application as app_module

    tracked_engines: dict[int, AsyncEngine] = {}
    original_build_database_engine = app_module.build_database_engine

    def _build_database_engine(settings):  # noqa: ANN001
        engine = original_build_database_engine(settings)
        tracked_engines[id(engine)] = engine
        return engine

    monkeypatch.setattr(app_module, "build_database_engine", _build_database_engine)
    yield

    if not tracked_engines:
        return

    async def _dispose_tracked_engines() -> None:
        for engine in tracked_engines.values():
            await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_dispose_tracked_engines())
    finally:
        loop.close()
