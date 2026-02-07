import dataclasses
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from opencode_a2a.app import create_app
from opencode_a2a.config import Settings

TEST_JWT_ISSUER = "test-issuer"
TEST_JWT_AUDIENCE = "test-audience"


@pytest.fixture
def rsa_keypair_pem() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


@pytest.fixture
def jwt_settings(rsa_keypair_pem):
    _private_pem, public_pem = rsa_keypair_pem
    return Settings(
        opencode_base_url="http://localhost:4096",
        opencode_directory=None,
        opencode_provider_id=None,
        opencode_model_id=None,
        opencode_agent=None,
        opencode_system=None,
        opencode_variant=None,
        opencode_timeout=120.0,
        opencode_timeout_stream=None,
        a2a_public_url="http://localhost:8000",
        a2a_title="Test Agent",
        a2a_description="Test Description",
        a2a_version="0.1.0",
        a2a_protocol_version="0.3.0",
        a2a_streaming=True,
        a2a_log_level="INFO",
        a2a_log_payloads=False,
        a2a_log_body_limit=0,
        a2a_host="127.0.0.1",
        a2a_port=8000,
        a2a_jwt_secret=public_pem,
        a2a_jwt_algorithm="RS256",
        a2a_jwt_issuer=TEST_JWT_ISSUER,
        a2a_jwt_audience=TEST_JWT_AUDIENCE,
        a2a_jwt_scope_match="any",
        a2a_oauth_authorization_url=None,
        a2a_oauth_token_url=None,
        a2a_oauth_metadata_url=None,
        a2a_oauth_scopes={},
    )


def _jwt_payload(**overrides):
    payload = {
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "exp": int(time.time()) + 3600,
    }
    payload.update(overrides)
    return payload


def test_jwt_auth_success(jwt_settings, rsa_keypair_pem):
    app = create_app(jwt_settings)
    client = TestClient(app)
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(_jwt_payload(), private_pem, algorithm="RS256")
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401


def test_jwt_auth_failure_invalid_secret(jwt_settings):
    app = create_app(jwt_settings)
    client = TestClient(app)
    wrong_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_private_pem = wrong_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    token = jwt.encode(_jwt_payload(), wrong_private_pem, algorithm="RS256")
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


def test_public_route_no_auth(jwt_settings):
    app = create_app(jwt_settings)
    client = TestClient(app)
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200


def test_jwt_auth_failure_missing_scope(jwt_settings, rsa_keypair_pem):
    # Setup settings with required scopes
    jwt_settings = dataclasses.replace(jwt_settings, a2a_oauth_scopes={"required-scope": ""})
    app = create_app(jwt_settings)
    client = TestClient(app)
    # Token with wrong scope
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        _jwt_payload(scope="other-scope"),
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_jwt_auth_success_with_scope(jwt_settings, rsa_keypair_pem):
    # Setup settings with required scopes
    jwt_settings = dataclasses.replace(jwt_settings, a2a_oauth_scopes={"required-scope": ""})
    app = create_app(jwt_settings)
    client = TestClient(app)
    # Token with correct scope
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        _jwt_payload(scope="required-scope"),
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401
    assert response.status_code != 403


def test_jwt_auth_failure_missing_exp(jwt_settings, rsa_keypair_pem):
    app = create_app(jwt_settings)
    client = TestClient(app)
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        {"iss": TEST_JWT_ISSUER, "aud": TEST_JWT_AUDIENCE},
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or expired token"


def test_jwt_auth_success_with_scope_list(jwt_settings, rsa_keypair_pem):
    jwt_settings = dataclasses.replace(jwt_settings, a2a_oauth_scopes={"required-scope": ""})
    app = create_app(jwt_settings)
    client = TestClient(app)
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        _jwt_payload(scope=["required-scope", "other-scope"]),
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401
    assert response.status_code != 403


def test_jwt_auth_success_with_scp_claim(jwt_settings, rsa_keypair_pem):
    jwt_settings = dataclasses.replace(jwt_settings, a2a_oauth_scopes={"required-scope": ""})
    app = create_app(jwt_settings)
    client = TestClient(app)
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        _jwt_payload(scp=["required-scope"]),
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401
    assert response.status_code != 403


def test_jwt_mode_requires_audience(jwt_settings):
    settings = dataclasses.replace(jwt_settings, a2a_jwt_audience=None)
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_jwt_scope_match_all_requires_all_scopes(jwt_settings, rsa_keypair_pem):
    jwt_settings = dataclasses.replace(
        jwt_settings,
        a2a_oauth_scopes={"scope-a": "", "scope-b": ""},
        a2a_jwt_scope_match="all",
    )
    app = create_app(jwt_settings)
    client = TestClient(app)
    private_pem, _public_pem = rsa_keypair_pem
    token = jwt.encode(
        _jwt_payload(scope=["scope-a"]),
        private_pem,
        algorithm="RS256",
    )
    response = client.get("/v1/tasks", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
