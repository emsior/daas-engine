"""Cockpit schedule requests must pass the credentialed PUT preflight."""
from __future__ import annotations


def test_cockpit_preflight_allows_put_for_the_schedule(cloud_client, cloud_settings):
    origin = cloud_settings.daas_cockpit_origin
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
    }
    response = cloud_client.options("/api/v1/orgs/TEST-org/schedule", headers=headers)
    assert response.status_code == 200, response.text
    assert "PUT" in response.headers["access-control-allow-methods"].split(", ")
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"

    foreign = cloud_client.options("/api/v1/orgs/TEST-org/schedule",
                                   headers={**headers, "Origin": "https://foreign.example.test"})
    assert "access-control-allow-origin" not in foreign.headers
