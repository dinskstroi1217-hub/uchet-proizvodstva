from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import Settings, create_app

TOKEN = "test-token"
KEY = {"key": "tablet-key"}
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EXAMPLE_CATALOG = Path(__file__).parent.parent / "catalog.example.json"
TODAY = date.today().isoformat()


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        db_path=str(tmp_path / "test.sqlite3"),
        api_token=TOKEN,
        allowed_serials={"TEST0001"},
        catalog_path=str(EXAMPLE_CATALOG),
        tablet_key=KEY["key"],
    )
    return TestClient(create_app(settings))


def punch(client, pin, status=1, at=None):
    at = at or f"{TODAY} 17:05:00"
    client.post(
        "/iclock/cdata", params={"SN": "TEST0001", "table": "ATTLOG"}, content=f"{pin}\t{at}\t{status}\t15\n"
    )


def poll(client, after):
    return client.get("/tablet/TEST0001/poll", params={**KEY, "after": after}).json()


def start(client):
    return poll(client, -1)["after"]


def test_page_needs_key(client):
    assert client.get("/tablet/TEST0001").status_code == 403
    assert client.get("/tablet/TEST0001", params={"key": "wrong"}).status_code == 403
    assert "Бригадир, отметьтесь" in client.get("/tablet/TEST0001", params=KEY).text


def test_first_poll_skips_old_punches(client):
    punch(client, "101")
    after = start(client)
    assert poll(client, after)["event"] is None


def test_worker_punch_is_only_acknowledged(client):
    after = start(client)
    punch(client, "102")
    event = poll(client, after)["event"]
    assert event == {"kind": "worker", "name": "Формовщик Один", "status_name": "уход"}


def test_foreman_enters_output_of_own_brigade(client):
    after = start(client)
    punch(client, "101")
    data = poll(client, after)
    event = data["event"]
    assert event["kind"] == "foreman" and event["brigade"] == "Бригада колец"
    assert [g["name"] for g in event["groups"]] == ["Кольца (КС)", "Плиты днища (ПН)", "Плиты перекрытия (ПП)", "Доборные/прочие"]
    assert "КС 15.9" in event["groups"][0]["products"]
    assert all("ФБС" not in p for g in event["groups"] for p in g["products"])

    saved = client.post(
        "/tablet/TEST0001/output", params=KEY, json={"session": event["session"], "qty": {"КС 15.9": 12, "ПП 15.1": 4}}
    )
    assert saved.json() == {"saved": 2, "work_date": TODAY}

    rows = client.get("/api/outputs", params={"day": TODAY}, headers=AUTH).json()
    assert [(r["brigade_name"], r["product"], r["qty"]) for r in rows] == [
        ("Бригада колец", "КС 15.9", 12),
        ("Бригада колец", "ПП 15.1", 4),
    ]

    # Повторная отметка показывает уже введённое, новый ввод заменяет прежний целиком.
    punch(client, "101", at=f"{TODAY} 17:20:00")
    again = poll(client, data["after"])["event"]
    assert again["qty"] == {"КС 15.9": 12, "ПП 15.1": 4}
    client.post("/tablet/TEST0001/output", params=KEY, json={"session": again["session"], "qty": {"КС 15.9": 10}})
    rows = client.get("/api/outputs", params={"day": TODAY}, headers=AUTH).json()
    assert [(r["product"], r["qty"]) for r in rows] == [("КС 15.9", 10)]


def test_foreign_product_and_bad_session_rejected(client):
    after = start(client)
    punch(client, "101")
    session = poll(client, after)["event"]["session"]
    foreign = client.post("/tablet/TEST0001/output", params=KEY, json={"session": session, "qty": {"ФБС 2400.300.600": 3}})
    assert foreign.status_code == 422
    negative = client.post("/tablet/TEST0001/output", params=KEY, json={"session": session, "qty": {"КС 15.9": -1}})
    assert negative.status_code == 422
    unknown = client.post("/tablet/TEST0001/output", params=KEY, json={"session": "nope", "qty": {"КС 15.9": 1}})
    assert unknown.status_code == 409
    other_device = client.post("/tablet/OTHER/output", params=KEY, json={"session": session, "qty": {"КС 15.9": 1}})
    assert other_device.status_code == 409


def test_outputs_need_token(client):
    assert client.get("/api/outputs", params={"day": TODAY}).status_code == 401
