"""The published problem schema must describe the real API error responses."""
from __future__ import annotations

import json

import pytest
from fastapi.openapi.utils import get_openapi

from app.api.problems import problem_body
from app.main import create_app
from tests.conftest import TEST_API_KEY
from tests.v1_helpers import API, key_headers, seed_key, seed_org, user_headers

PROBLEM_TYPE = "application/problem+json"
PROBLEM_REF = {"$ref": "#/components/schemas/Problem"}
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def assert_schema(value, schema, components):
    """Check the JSON types, required fields and references used by Problem."""
    if "$ref" in schema:
        assert schema["$ref"].startswith("#/components/schemas/")
        return assert_schema(value, components[schema["$ref"].rsplit("/", 1)[1]], components)
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            try:
                assert_schema(value, option, components)
            except AssertionError:
                continue
            return
        pytest.fail(f"{value!r} does not match {schema!r}")
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    assert type(value) is types[kind], (value, schema)
    if kind == "object":
        assert set(schema.get("required", [])) <= value.keys()
        properties = schema.get("properties", {})
        for name, field in properties.items():
            if name in value:
                assert_schema(value[name], field, components)
        if schema.get("additionalProperties") is False:
            assert value.keys() <= properties.keys()
    elif kind == "array":
        for item in value:
            assert_schema(item, schema["items"], components)


@pytest.mark.parametrize("mode", ["cloud", "selfhost"])
def test_every_v1_operation_declares_problem_validation_errors(cloud_settings, mode):
    settings = cloud_settings.model_copy(update={"daas_mode": mode, "daas_api_key": TEST_API_KEY})
    document = create_app(settings).openapi()
    checked = []
    for path, item in document["paths"].items():
        if not (path.startswith("/api/v1/") or path == "/mcp"):
            continue
        for method, operation in item.items():
            if method not in METHODS:
                continue
            checked.append((path, method))
            response = operation["responses"].get("422", {})
            assert response.get("content") == {PROBLEM_TYPE: {"schema": PROBLEM_REF}}, (path, method, response)
            assert response["description"] == "Validation error (RFC 9457 problem details)"
            assert "HTTPValidationError" not in json.dumps(operation)
    assert checked


@pytest.mark.parametrize("mode", ["cloud", "selfhost"])
def test_other_errors_gain_content_without_changing_legacy_or_explicit_content(cloud_settings, mode):
    settings = cloud_settings.model_copy(update={"daas_mode": mode, "daas_api_key": TEST_API_KEY})
    app = create_app(settings)
    original = get_openapi(title=app.title, version=app.version, routes=app.routes)
    document = app.openapi()
    added_content = []
    for path, item in original["paths"].items():
        if not (path.startswith("/api/v1/") or path == "/mcp"):
            assert document["paths"][path] == item
            continue
        for method, operation in item.items():
            if method not in METHODS:
                continue
            for status, response in operation["responses"].items():
                if status == "422":
                    continue
                actual = document["paths"][path][method]["responses"][status]
                if status.startswith(("4", "5")) and "content" not in response:
                    assert actual == {**response, "content": {PROBLEM_TYPE: {"schema": PROBLEM_REF}}}
                    added_content.append((path, method, status))
                else:
                    assert actual == response
    assert added_content
    # Legacy routes still reference FastAPI's validation schemas in selfhost.
    for name in ("HTTPValidationError", "ValidationError"):
        assert (name in document["components"]["schemas"]) is (mode == "selfhost")


@pytest.mark.parametrize("status", [422, 404, 403, 401])
def test_real_error_response_matches_the_published_problem_schema(cloud_client, cloud_settings, status):
    org = seed_org(cloud_settings, name="TEST-contract")
    base = f"{API}/orgs/{org.org_id}"
    if status == 422:
        response = cloud_client.post(f"{base}/api-keys", json={},
                                     headers=user_headers(cloud_settings, org.user_id))
    elif status == 404:
        response = cloud_client.get(f"{base}/runs/TEST-missing", headers=key_headers(org.api_key))
    elif status == 403:
        _, limited = seed_key(cloud_settings, org.org_id, scopes=["org:read"])
        response = cloud_client.get(f"{base}/api-keys", headers=key_headers(limited))
    else:
        response = cloud_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})

    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_TYPE
    body = response.json()
    assert body["status"] == status
    assert body["request_id"] == response.headers["x-request-id"]
    components = cloud_client.app.openapi()["components"]["schemas"]
    assert "Problem" in components
    assert_schema(body, components["Problem"], components)
    if status == 422:
        assert body["errors"]
        for issue in body["errors"]:
            assert set(issue) == {"loc", "msg", "type"}
            assert_schema(issue, components["ValidationIssue"], components)
    else:
        assert "errors" not in body
    if status == 403:
        assert body["required_scope"] == "keys:manage"


def test_problem_schema_allows_extensions_and_null_request_id(cloud_settings):
    components = create_app(cloud_settings).openapi()["components"]["schemas"]
    schema = components["Problem"]
    assert schema["additionalProperties"] is True
    assert set(schema["required"]) == {"type", "title", "status", "detail", "instance", "request_id", "code"}
    body = problem_body(403, "forbidden", "insufficient permissions", "/api/v1/test", None,
                        {"required_scope": "keys:manage", "status": 200})
    assert body == {
        "type": "urn:daas:problem:forbidden", "title": "Forbidden", "status": 403,
        "detail": "insufficient permissions", "instance": "/api/v1/test", "request_id": None,
        "code": "forbidden", "required_scope": "keys:manage",
    }
    assert_schema(body, schema, components)
