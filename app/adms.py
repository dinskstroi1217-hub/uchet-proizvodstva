"""Приём данных с терминалов ZKTeco по протоколу ADMS (PUSH).

Терминал сам обращается к серверу по HTTP:
  GET  /iclock/cdata      — при включении запрашивает настройки обмена;
  POST /iclock/cdata      — присылает данные, отметки идут с table=ATTLOG;
  GET  /iclock/getrequest — каждые несколько секунд спрашивает, нет ли для него команд;
  POST /iclock/devicecmd  — сообщает результат выполнения команды.
Ответы — простой текст. Терминал считает обмен успешным, только получив «OK».
"""

import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

log = logging.getLogger("adms")

router = APIRouter(prefix="/iclock")


def parse_attlog(body: str) -> list[dict]:
    """Строка отметки: PIN, время, состояние (0 — приход, 1 — уход и т. д.), способ проверки, ... через табуляцию."""
    punches = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            log.warning("Пропущена непонятная строка отметки: %r", line)
            continue
        punches.append(
            {
                "pin": parts[0].strip(),
                "punched_at": parts[1].strip(),
                "status": _int(parts[2]) if len(parts) > 2 else None,
                "verify": _int(parts[3]) if len(parts) > 3 else None,
                "raw": line,
            }
        )
    return punches


def parse_device_replies(body: str) -> list[dict]:
    """Ответ на команды: по строке на команду, вида ID=5&Return=0&CMD=DATA."""
    replies = []
    for line in body.splitlines():
        fields = dict(parse_qsl(line.strip()))
        if "ID" in fields:
            replies.append({"id": _int(fields["ID"]), "return": _int(fields.get("Return", ""))})
    return replies


def _int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


def _device(request: Request) -> str:
    sn = request.query_params.get("SN", "").strip()
    if not sn:
        raise HTTPException(400, "SN is required")
    allowed = request.app.state.settings.allowed_serials
    if allowed and sn not in allowed:
        log.warning("Обращение от незнакомого терминала %s", sn)
        raise HTTPException(403, "Unknown device")
    request.app.state.db.touch_device(sn)
    return sn


def _text(body: str) -> PlainTextResponse:
    return PlainTextResponse(body)


@router.get("/cdata")
def handshake(request: Request):
    sn = _device(request)
    settings = request.app.state.settings
    stamp = request.app.state.db.attlog_stamp(sn) or "0"
    # Realtime=1: терминал шлёт каждую отметку сразу, а не пачкой по расписанию.
    return _text(
        f"GET OPTION FROM: {sn}\n"
        f"ATTLOGStamp={stamp}\n"
        "OPERLOGStamp=9999\n"
        "ATTPHOTOStamp=None\n"
        "ErrorDelay=30\n"
        f"Delay={settings.poll_delay}\n"
        "TransTimes=00:00;12:00\n"
        "TransInterval=1\n"
        "TransFlag=TransData AttLog OpLog EnrollUser ChgUser\n"
        f"TimeZone={settings.timezone_hours}\n"
        "Realtime=1\n"
        "Encrypt=None\n"
    )


@router.post("/cdata")
async def upload(request: Request):
    sn = _device(request)
    db = request.app.state.db
    table = request.query_params.get("table", "")
    body = (await request.body()).decode("utf-8", errors="replace")

    if table == "ATTLOG":
        punches = parse_attlog(body)
        added = db.add_punches(sn, punches)
        stamp = request.query_params.get("Stamp")
        if stamp:
            db.set_attlog_stamp(sn, stamp)
        log.info("Терминал %s: получено отметок %d, новых %d", sn, len(punches), added)
        return _text(f"OK: {len(punches)}")

    if table == "options":
        db.set_device_info(sn, body)
        return _text("OK")

    # Журнал операций, фото и прочее пока только подтверждаем, чтобы терминал не слал их повторно.
    log.info("Терминал %s: таблица %s, %d байт, не обрабатывается", sn, table or "?", len(body))
    return _text("OK")


@router.get("/getrequest")
def get_request(request: Request):
    sn = _device(request)
    commands = request.app.state.db.take_pending_commands(sn)
    if not commands:
        return _text("OK")
    return _text("".join(f"C:{c['id']}:{c['command']}\n" for c in commands))


@router.post("/devicecmd")
async def device_cmd(request: Request):
    _device(request)
    body = (await request.body()).decode("utf-8", errors="replace")
    for reply in parse_device_replies(body):
        if reply["id"] is not None:
            request.app.state.db.finish_command(reply["id"], reply["return"])
    return _text("OK")
