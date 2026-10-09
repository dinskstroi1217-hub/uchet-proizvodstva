"""Планшет бригадира у терминала.

Планшет открывает страницу /tablet/{SN терминала}?key=... и раз в секунду-две спрашивает, кто отметился.
Если отметился бригадир, сервер открывает сеанс ввода и отдаёт изделия его бригады. Бригадир вводит штуки,
планшет отправляет их с токеном сеанса. Рабочим планшет только подтверждает отметку.
"""

import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api import STATUS_NAMES

router = APIRouter(prefix="/tablet")

PAGE = Path(__file__).parent / "static" / "tablet.html"


def _check_key(request: Request) -> None:
    key = request.app.state.settings.tablet_key
    if not key or not secrets.compare_digest(request.query_params.get("key", ""), key):
        raise HTTPException(403, "Неверный ключ планшета")


class Output(BaseModel):
    session: str
    qty: dict[str, int] = Field(default_factory=dict)


@router.get("/{sn}", response_class=HTMLResponse)
def page(request: Request, sn: str):
    _check_key(request)
    return PAGE.read_text(encoding="utf-8")


@router.get("/{sn}/poll")
def poll(request: Request, sn: str, after: int = -1):
    """after=-1 — первый запрос: планшет узнаёт, с какой отметки начинать, и прошлые отметки не показывает."""
    _check_key(request)
    db = request.app.state.db
    if after < 0:
        return {"after": db.last_punch_id(sn), "event": None}

    settings = request.app.state.settings
    punches = db.fresh_punches(sn, after, settings.tablet_fresh_seconds)
    if not punches:
        return {"after": after, "event": None}

    # Если за время между опросами отметились несколько человек, показываем последнего.
    punch = punches[-1]
    catalog = request.app.state.catalog
    name = catalog.employees.get(punch["pin"], f"Таб. № {punch['pin']}")
    brigade = catalog.brigade_of_foreman(punch["pin"])
    if not brigade:
        event = {"kind": "worker", "name": name, "status_name": STATUS_NAMES.get(punch["status"], "отметка")}
        return {"after": punch["id"], "event": event}

    work_date = punch["punched_at"][:10]
    token = db.open_tablet_session(
        secrets.token_urlsafe(16), punch["id"], sn, brigade.id, brigade.foreman_pin, work_date,
        settings.tablet_session_seconds,
    )
    products = catalog.products_of(brigade)
    entered = {row["product"]: row["qty"] for row in db.outputs(work_date, brigade.id)}
    event = {
        "kind": "foreman",
        "name": name,
        "brigade": brigade.name,
        "work_date": work_date,
        "session": token,
        "groups": [
            {"name": group, "products": [p["name"] for p in products if p["group"] == group]}
            for group in brigade.groups
        ],
        "qty": entered,
    }
    return {"after": punch["id"], "event": event}


@router.post("/{sn}/output")
def save_output(request: Request, sn: str, output: Output):
    _check_key(request)
    db = request.app.state.db
    session = db.tablet_session(output.session, sn)
    if not session:
        raise HTTPException(409, "Время ввода вышло. Отметьтесь на терминале ещё раз.")

    catalog = request.app.state.catalog
    brigade = catalog.brigade(session["brigade_id"])
    allowed = {p["name"] for p in catalog.products_of(brigade)} if brigade else set()
    for name, n in output.qty.items():
        if name not in allowed:
            raise HTTPException(422, f"Изделие «{name}» не из группы этой бригады")
        if not 0 <= n <= 10000:
            raise HTTPException(422, f"Недопустимое количество для «{name}»: {n}")

    db.save_output(session["work_date"], session["brigade_id"], session["foreman_pin"], output.qty)
    return {"saved": sum(1 for n in output.qty.values() if n > 0), "work_date": session["work_date"]}
