"""D12 (A02 S-a02-04): walidacyjny 422 trasy legacy `POST /run` nie odbija wartosci przeslanej przez klienta.

`docs/API.md`: "the submitted value is never echoed". Wczesniej legacy `/run` zwracal surowe bledy pydantic z polem
`input` (cale cialo/wartosc). Po poprawce 422 ma `code=validation_error`, liste `detail` z `loc`/`msg`/`type` i
`request_id`, ale BEZ `input`. (Zmiana content-type na `application/problem+json` jak w v1 wymagalaby zmiany
globalnego handlera w `app/main.py` i testu `tests/test_observability.py` — poza przydzialem; patrz flaga PR.)
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _assert_no_input_echo(detail: list[dict]) -> None:
    for err in detail:
        assert "input" not in err, err
        assert set(err) <= {"loc", "msg", "type"}, err


def test_reversed_period_is_422_without_echoing_values(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "period_from": "2026-08-09",
                                  "period_to": "2026-08-03"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation_error" and isinstance(body["detail"], list)
    assert body["request_id"] == r.headers["x-request-id"]
    # wartosci klienta nie moga sie pojawic w odpowiedzi
    assert "2026-08-09" not in r.text and "2026-08-03" not in r.text
    _assert_no_input_echo(body["detail"])


def test_bad_date_format_is_422_without_echo(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "period_from": "01.08.2026"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "01.08.2026" not in r.text
    _assert_no_input_echo(body["detail"])


def test_both_sources_is_422_without_echoing_source_path(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo", "upload_id": "0" * 32,
                                  "source_path": "poufna/sciezka"})
    assert r.status_code == 422
    assert "poufna/sciezka" not in r.text
    _assert_no_input_echo(r.json()["detail"])


def test_empty_body_keeps_legacy_envelope(client: TestClient):
    """Puste cialo -> 422 w dotychczasowym ksztalcie legacy (detail=lista, code, request_id); bez echa."""
    r = client.post("/run", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error" and isinstance(body["detail"], list)
    assert body["request_id"] == r.headers["x-request-id"]
    _assert_no_input_echo(body["detail"])


def test_malformed_json_body_is_422_without_echo(client: TestClient):
    r = client.post("/run", content=b"{not valid json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert "not valid json" not in r.text


def test_valid_run_still_succeeds(client: TestClient):
    r = client.post("/run", json={"pipeline": "ecommerce_demo"})
    assert r.status_code == 200, r.text
    assert r.json()["run_status"] in {"SUCCESS", "PARTIAL"}
