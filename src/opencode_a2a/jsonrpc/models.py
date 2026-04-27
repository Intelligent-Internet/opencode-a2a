from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class JSONRPCBaseModel(BaseModel):
    model_config = {
        "extra": "allow",
        "populate_by_name": True,
        "arbitrary_types_allowed": True,
    }


class JSONRPCError(JSONRPCBaseModel):
    code: int
    message: str
    data: Any | None = None


class JSONRPCRequest(JSONRPCBaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] | None = None
    id: str | int | None = None


class JSONRPCErrorResponse(JSONRPCBaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    error: JSONRPCError
