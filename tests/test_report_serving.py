"""Report downloads keep the hashed snapshot; selfhost documents its redacted run input."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.pipelines.runner import PipelineRunner


@pytest.fixture()
def report_run_id(client: TestClient) -> str:
    response = client.post("/run", json={"pipeline": "ecommerce_demo", "force_mock": True})
    assert response.status_code == 200, response.text
    assert response.json()["run_status"] == "SUCCESS"
    return response.json()["run_id"]


@pytest.mark.parametrize("fmt", ["md", "json", "html"])
@pytest.mark.parametrize("route", ["/reports/ecommerce_demo/latest", "/reports/run/{run_id}"])
def test_markdown_and_json_reports_are_served_from_the_hashed_bytes(
    client: TestClient, report_run_id: str, monkeypatch: pytest.MonkeyPatch, route: str, fmt: str,
):
    read_report_bytes = routes.read_report_bytes
    original_bodies: list[bytes] = []

    def replace_after_read(path: Path) -> tuple[bytes, str]:
        body, digest = read_report_bytes(path)
        original_bodies.append(body)
        path.write_bytes(b"TEST-replacement-after-hashing")
        return body, digest

    monkeypatch.setattr(routes, "read_report_bytes", replace_after_read)
    response = client.get(route.format(run_id=report_run_id), params={"fmt": fmt})

    assert response.status_code == 200, response.text
    original_body, = original_bodies
    assert response.headers["X-Report-SHA256"] == hashlib.sha256(response.content).hexdigest()
    assert response.headers["X-Report-SHA256"] == hashlib.sha256(original_body).hexdigest()
    assert response.content == original_body
    assert response.headers["content-length"] == str(len(original_body))


@pytest.mark.parametrize("fmt,content_type", [
    ("md", "text/markdown; charset=utf-8"),
    ("json", "application/json"),
])
@pytest.mark.parametrize("route", ["/reports/ecommerce_demo/latest", "/reports/run/{run_id}"])
def test_report_download_headers_are_preserved(
    client: TestClient, runner: PipelineRunner, report_run_id: str, route: str, fmt: str, content_type: str,
):
    if route.endswith("latest"):
        filename = f"ecommerce_demo_latest.{fmt}"
    else:
        row = runner.db.get_run(report_run_id)
        filename = Path(row["report_path" if fmt == "md" else "report_json_path"]).name

    response = client.get(route.format(run_id=report_run_id), params={"fmt": fmt})

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert response.headers["content-type"] == content_type
    assert response.headers["content-length"] == str(len(response.content))
    for name, value in routes.REPORT_HEADERS.items():
        assert response.headers[name] == value


def test_selfhost_openapi_describes_the_run_request_body(client: TestClient):
    operation = client.app.openapi()["paths"]["/run"]["post"]
    request_body = operation["requestBody"]
    assert request_body["required"] is True
    schema = request_body["content"]["application/json"]["schema"]
    assert {"pipeline", "upload_id", "force_mock", "notify", "source_path"} <= schema["properties"].keys()
    assert schema["required"] == ["pipeline"]
    assert schema["properties"]["pipeline"]["enum"] == ["cs2_demo", "ecommerce_demo", "uksc_evidence"]
    assert schema["properties"]["force_mock"]["default"] is False
    assert schema["properties"]["notify"]["default"] is False
    assert "422" in operation["responses"]


@pytest.mark.parametrize("content", ["{}", '{"pipeline":"TEST-INVALID-PIPELINE"}', '{"pipeline":'])
def test_selfhost_openapi_describes_legacy_run_validation_errors(client: TestClient, content: str):
    operation = client.app.openapi()["paths"]["/run"]["post"]
    error = operation["responses"]["422"]
    assert error["description"]
    assert set(error["content"]) == {"application/json"}
    schema = error["content"]["application/json"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"detail", "code", "request_id"}
    properties = schema["properties"]
    assert properties["detail"]["type"] == "array"
    detail = properties["detail"]["items"]
    assert detail["type"] == "object"
    assert set(detail["required"]) == {"loc", "msg", "type"}
    assert detail["properties"] == {
        "loc": {"type": "array", "items": {"type": "string"}},
        "msg": {"type": "string"},
        "type": {"type": "string"},
    }
    assert properties["code"] == {"type": "string", "const": "validation_error"}
    assert properties["request_id"]["type"] == "string"

    response = client.post("/run", content=content, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert set(body) == {"detail", "code", "request_id"}
    assert body["code"] == "validation_error"
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert body["detail"]
    for item in body["detail"]:
        assert set(item) == {"loc", "msg", "type"}
        assert isinstance(item["loc"], list) and all(isinstance(part, str) for part in item["loc"])
        assert isinstance(item["msg"], str) and isinstance(item["type"], str)
    assert "TEST-INVALID-PIPELINE" not in response.text
