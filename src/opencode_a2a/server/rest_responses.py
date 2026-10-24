from __future__ import annotations

from a2a.utils.constants import A2A_JSON_MEDIA_TYPE
from fastapi.responses import JSONResponse


class A2AJSONResponse(JSONResponse):
    """Successful HTTP+JSON response using the A2A 1.0 media type."""

    media_type = A2A_JSON_MEDIA_TYPE
