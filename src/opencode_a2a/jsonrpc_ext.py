from __future__ import annotations

import logging
from typing import Any

from a2a.server.apps.jsonrpc.fastapi_app import A2AFastAPIApplication
from a2a.types import (
    A2AError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONRPCRequest,
)
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

from .opencode_client import OpencodeClient

logger = logging.getLogger(__name__)


class OpencodeSessionQueryJSONRPCApplication(A2AFastAPIApplication):
    """Extend A2A JSON-RPC endpoint with OpenCode session query methods.

    These methods are optional (declared via AgentCard.capabilities.extensions) and do
    not require additional private REST endpoints.
    """

    def __init__(
        self,
        *args: Any,
        opencode_client: OpencodeClient,
        methods: dict[str, str],
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._opencode_client = opencode_client
        self._method_list_sessions = methods["list_sessions"]
        self._method_get_session_messages = methods["get_session_messages"]

    async def _handle_requests(self, request: Request) -> Response:
        # Fast path: sniff method first then either handle here or delegate.
        request_id: str | int | None = None
        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get("id")
                if request_id is not None and not isinstance(request_id, str | int):
                    request_id = None

            if not self._allowed_content_length(request):
                return self._generate_error_response(
                    request_id,
                    A2AError(root=InvalidRequestError(message="Payload too large")),
                )

            base_request = JSONRPCRequest.model_validate(body)
        except Exception:
            # Delegate to base implementation for consistent error handling.
            return await super()._handle_requests(request)

        if base_request.method not in {
            self._method_list_sessions,
            self._method_get_session_messages,
        }:
            return await super()._handle_requests(request)

        params = base_request.params or {}
        if not isinstance(params, dict):
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InvalidParamsError(message="params must be an object")),
            )

        query: dict[str, Any] = {}
        raw_query = params.get("query")
        if isinstance(raw_query, dict):
            query.update(raw_query)

        # Pagination contract: page/size only.
        if "cursor" in params or "limit" in params:
            return self._generate_error_response(
                base_request.id,
                A2AError(
                    root=InvalidParamsError(
                        message=(
                            "Only page/size pagination is supported (cursor/limit not supported)."
                        )
                    )
                ),
            )

        for key in ("page", "size"):
            value = params.get(key)
            if value is None:
                continue
            query[key] = value

        try:
            if base_request.method == self._method_list_sessions:
                result = await self._opencode_client.list_sessions(params=query)
            else:
                session_id = params.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    return self._generate_error_response(
                        base_request.id,
                        A2AError(
                            root=InvalidParamsError(message="Missing required params.session_id")
                        ),
                    )
                result = await self._opencode_client.list_messages(session_id, params=query)
        except Exception as exc:
            logger.exception("OpenCode session query JSON-RPC method failed")
            return self._generate_error_response(
                base_request.id,
                A2AError(root=InternalError(message=str(exc))),
            )

        # Notifications (id omitted) should not yield a response.
        if base_request.id is None:
            return Response(status_code=204)

        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": base_request.id,
                "result": result,
            }
        )
