import time

import httpx
import jwt
import pytest

from tests.helpers import make_settings


def _make_jwt(secret: str, **overrides) -> str:
    payload = {
        "iss": "test-issuer",
        "aud": "test-audience",
        "exp": int(time.time()) + 300,
        "sub": "user-1",
    }
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_auth_bearer_mode_accepts_valid_token():
    import opencode_a2a_serve.app as app_module

    app = app_module.create_app(make_settings(a2a_bearer_token="token-1"))
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer token-1"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers=headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_jwt_mode_accepts_valid_token():
    import opencode_a2a_serve.app as app_module

    secret = "test-jwt-signing-key-material-for-ci-123456"  # pragma: allowlist secret
    app = app_module.create_app(
        make_settings(
            a2a_auth_mode="jwt",
            a2a_bearer_token=None,
            a2a_jwt_secret=secret,
            a2a_jwt_algorithm="HS256",
            a2a_jwt_issuer="test-issuer",
            a2a_jwt_audience="test-audience",
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {_make_jwt(secret)}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers=headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_jwt_mode_rejects_missing_required_scope():
    import opencode_a2a_serve.app as app_module

    secret = "test-jwt-signing-key-material-for-ci-123456"  # pragma: allowlist secret
    app = app_module.create_app(
        make_settings(
            a2a_auth_mode="jwt",
            a2a_bearer_token=None,
            a2a_jwt_secret=secret,
            a2a_jwt_algorithm="HS256",
            a2a_jwt_issuer="test-issuer",
            a2a_jwt_audience="test-audience",
            a2a_required_scopes={"opencode"},
            a2a_jwt_scope_match="any",
        )
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {_make_jwt(secret, scope='other')}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers=headers)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_auth_jwt_mode_rejects_token_missing_exp():
    import opencode_a2a_serve.app as app_module

    secret = "test-jwt-signing-key-material-for-ci-123456"  # pragma: allowlist secret
    app = app_module.create_app(
        make_settings(
            a2a_auth_mode="jwt",
            a2a_bearer_token=None,
            a2a_jwt_secret=secret,
            a2a_jwt_algorithm="HS256",
            a2a_jwt_issuer="test-issuer",
            a2a_jwt_audience="test-audience",
        )
    )
    token = jwt.encode(
        {
            "iss": "test-issuer",
            "aud": "test-audience",
            "sub": "user-1",
        },
        secret,
        algorithm="HS256",
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers=headers)
        assert resp.status_code == 401
