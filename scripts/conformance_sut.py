from __future__ import annotations

import os

import uvicorn
from tests.support.helpers import DummyChatOpencodeUpstreamClient
from tests.support.settings import make_settings

import opencode_a2a.server.application as app_module


def main() -> None:
    port = int(os.environ["CONFORMANCE_SUT_PORT"])
    app_module.OpencodeUpstreamClient = DummyChatOpencodeUpstreamClient
    app = app_module.create_app(
        make_settings(
            test_bearer_token=os.environ["CONFORMANCE_AUTH_TOKEN"],
            a2a_host="127.0.0.1",
            a2a_port=port,
            a2a_public_url=os.environ["CONFORMANCE_SUT_URL"],
            a2a_rate_limit_max_requests=10_000,
        )
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
