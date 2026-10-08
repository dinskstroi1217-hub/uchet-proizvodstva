"""Симулятор терминала ZKTeco: ведёт себя как настоящий терминал с ADMS, чтобы проверить сервер без железа.

Пример:
    python tools/terminal_sim.py --server http://localhost:8080 --sn TEST0001 --pin 101 --status 0
"""

import argparse
from datetime import datetime

import httpx


def run(server: str, sn: str, pin: str, status: int, client: httpx.Client | None = None) -> None:
    client = client or httpx.Client(base_url=server, timeout=10)

    options = client.get("/iclock/cdata", params={"SN": sn, "options": "all", "pushver": "2.4.1"})
    options.raise_for_status()
    print("Настройки от сервера:\n" + options.text)

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    punched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 15 — проверка по лицу.
    line = f"{pin}\t{punched_at}\t{status}\t15\t0\t0\t0\n"
    sent = client.post("/iclock/cdata", params={"SN": sn, "table": "ATTLOG", "Stamp": stamp}, content=line)
    sent.raise_for_status()
    print(f"Отметка {pin} {punched_at}: сервер ответил {sent.text!r}")

    commands = client.get("/iclock/getrequest", params={"SN": sn})
    commands.raise_for_status()
    replies = []
    for row in commands.text.splitlines():
        if row.startswith("C:"):
            _, command_id, command = row.split(":", 2)
            print(f"Команда {command_id}: {command!r}")
            replies.append(f"ID={command_id}&Return=0&CMD=DATA")
    if replies:
        client.post("/iclock/devicecmd", params={"SN": sn}, content="\n".join(replies)).raise_for_status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", default="http://localhost:8080")
    parser.add_argument("--sn", default="TEST0001")
    parser.add_argument("--pin", default="101")
    parser.add_argument("--status", type=int, default=0, help="0 — приход, 1 — уход")
    args = parser.parse_args()
    run(args.server, args.sn, args.pin, args.status)
