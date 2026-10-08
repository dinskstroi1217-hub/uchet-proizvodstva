"""API сервиса для бригадира, 1С и администратора. Доступ по токену из переменной окружения API_TOKEN."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

# Как на экране терминала («Статус события»). СУ — сверхурочно.
STATUS_NAMES = {0: "приход", 1: "уход", 2: "на перерыв", 3: "с перерыва", 4: "СУ приход", 5: "СУ уход"}


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    token = request.app.state.settings.api_token
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "Нужен токен доступа")


router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


class TerminalUser(BaseModel):
    pin: str = Field(pattern=r"^\d{1,9}$", description="Табельный номер на терминале")
    name: str = Field(max_length=24)


@router.get("/punches")
def punches(request: Request, day: date):
    """Отметки за день. Время — как его показывает терминал (местное)."""
    rows = request.app.state.db.punches(day.isoformat(), (day + timedelta(days=1)).isoformat())
    for row in rows:
        row["status_name"] = STATUS_NAMES.get(row["status"], "неизвестно")
    return rows


@router.post("/devices/{sn}/users")
def add_user(request: Request, sn: str, user: TerminalUser):
    """Ставит в очередь команду добавить сотрудника на терминал. Лицо регистрируется на самом терминале."""
    name = user.name.replace("\t", " ").replace("\n", " ")
    command_id = request.app.state.db.queue_command(sn, f"DATA UPDATE USERINFO PIN={user.pin}\tName={name}\tPri=0")
    return {"command_id": command_id}


@router.delete("/devices/{sn}/users/{pin}")
def delete_user(request: Request, sn: str, pin: str):
    if not pin.isdigit():
        raise HTTPException(422, "Табельный номер должен состоять из цифр")
    command_id = request.app.state.db.queue_command(sn, f"DATA DELETE USERINFO PIN={pin}")
    return {"command_id": command_id}


@router.get("/commands/{command_id}")
def command_status(request: Request, command_id: int):
    command = request.app.state.db.command(command_id)
    if not command:
        raise HTTPException(404, "Команда не найдена")
    return command
