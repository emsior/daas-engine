"""API key validation must not reflect untrusted scope values."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from tests.v1_helpers import API, seed_org, user_headers


def test_an_unknown_scope_is_not_echoed(cloud_client: TestClient, cloud_settings: Settings):
    org = seed_org(cloud_settings)
    sentinel = "CLIENT_SENTINEL_x9z"
    response = cloud_client.post(
        f"{API}/orgs/{org.org_id}/api-keys",
        json={"name": "TEST-key", "scopes": [sentinel]},
        headers=user_headers(cloud_settings, org.user_id),
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert sentinel not in response.text
    assert all(sentinel not in name and sentinel not in value for name, value in response.headers.items())
    assert response.json()["errors"] == [
        {"loc": ["body", "scopes"], "msg": "Value error, unknown scopes", "type": "value_error"},
    ]
