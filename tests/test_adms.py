from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.adms import parse_attlog
from app.main import Settings, create_app
from tools.terminal_sim import run as simulate

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "test.sqlite3"), api_token=TOKEN, allowed_serials={"TEST0001"})
    return TestClient(create_app(settings))


def send_attlog(client, body, stamp="1"):
    return client.post("/iclock/cdata", params={"SN": "TEST0001", "table": "ATTLOG", "Stamp": stamp}, content=body)


def test_handshake_returns_options(client):
    response = client.get("/iclock/cdata", params={"SN": "TEST0001", "options": "all"})
    assert response.status_code == 200
    assert response.text.startswith("GET OPTION FROM: TEST0001\n")
    assert "Realtime=1" in response.text
    assert "ATTLOGStamp=0" in response.text


def test_unknown_device_is_rejected(client):
    assert client.get("/iclock/cdata", params={"SN": "STRANGER"}).status_code == 403


def test_attlog_is_stored_and_duplicates_skipped(client):
    body = "101\t2026-10-08 07:51:12\t0\t15\t0\t0\t0\n102\t2026-10-08 07:55:40\t0\t15\t0\t0\t0\n"
    assert send_attlog(client, body, stamp="111").text == "OK: 2"
    # Терминал может прислать ту же пачку повторно, если не дождался ответа.
    assert send_attlog(client, body, stamp="111").text == "OK: 2"

    punches = client.get("/api/punches", params={"day": "2026-10-08"}, headers=AUTH).json()
    assert [(p["pin"], p["punched_at"], p["status_name"]) for p in punches] == [
        ("101", "2026-10-08 07:51:12", "приход"),
        ("102", "2026-10-08 07:55:40", "приход"),
    ]
    # После перезапуска терминал получит отметку, с которой продолжать.
    assert "ATTLOGStamp=111" in client.get("/iclock/cdata", params={"SN": "TEST0001"}).text


def test_punches_are_filtered_by_day(client):
    send_attlog(client, "101\t2026-10-07 18:02:00\t1\t15\n101\t2026-10-08 07:50:00\t0\t15\n")
    punches = client.get("/api/punches", params={"day": "2026-10-08"}, headers=AUTH).json()
    assert [p["punched_at"] for p in punches] == ["2026-10-08 07:50:00"]


def test_other_tables_are_acknowledged(client):
    response = client.post("/iclock/cdata", params={"SN": "TEST0001", "table": "OPERLOG"}, content="OPLOG 4\t0\t...")
    assert response.text == "OK"


def test_parse_attlog_skips_garbage():
    punches = parse_attlog("\n101\t2026-10-08 07:51:12\t0\t15\nмусор\n")
    assert len(punches) == 1
    assert punches[0]["verify"] == 15


def test_api_requires_token(client):
    assert client.get("/api/punches", params={"day": "2026-10-08"}).status_code == 401


def test_user_command_round_trip(client):
    command_id = client.post(
        "/api/devices/TEST0001/users", json={"pin": "101", "name": "Ivanov I."}, headers=AUTH
    ).json()["command_id"]

    pending = client.get("/iclock/getrequest", params={"SN": "TEST0001"}).text
    assert pending == f"C:{command_id}:DATA UPDATE USERINFO PIN=101\tName=Ivanov I.\tPri=0\n"
    # Отданная команда больше не повторяется.
    assert client.get("/iclock/getrequest", params={"SN": "TEST0001"}).text == "OK"

    client.post("/iclock/devicecmd", params={"SN": "TEST0001"}, content=f"ID={command_id}&Return=0&CMD=DATA")
    status = client.get(f"/api/commands/{command_id}", headers=AUTH).json()
    assert status["return_code"] == 0 and status["done_at"]


def test_simulator_against_server(client):
    client.post("/api/devices/TEST0001/users", json={"pin": "101", "name": "Test"}, headers=AUTH)
    simulate("", "TEST0001", "101", 0, client=client)
    punches = client.get("/api/punches", params={"day": date.today().isoformat()}, headers=AUTH).json()
    assert [p["pin"] for p in punches] == ["101"]
