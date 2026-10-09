"""Хранилище SQLite: терминалы, отметки, очередь команд для терминалов и выпуск бригад."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    sn            TEXT PRIMARY KEY,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    attlog_stamp  TEXT,
    info          TEXT
);

CREATE TABLE IF NOT EXISTS punches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_sn    TEXT NOT NULL,
    pin          TEXT NOT NULL,
    punched_at   TEXT NOT NULL,
    status       INTEGER,
    verify       INTEGER,
    received_at  TEXT NOT NULL,
    raw          TEXT NOT NULL,
    UNIQUE (device_sn, pin, punched_at)
);

CREATE INDEX IF NOT EXISTS punches_by_time ON punches (punched_at);

CREATE TABLE IF NOT EXISTS commands (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_sn    TEXT NOT NULL,
    command      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sent_at      TEXT,
    done_at      TEXT,
    return_code  INTEGER
);

-- Сеанс ввода на планшете: открывается отметкой бригадира, живёт несколько минут.
CREATE TABLE IF NOT EXISTS tablet_sessions (
    token        TEXT PRIMARY KEY,
    punch_id     INTEGER NOT NULL UNIQUE,
    device_sn    TEXT NOT NULL,
    brigade_id   TEXT NOT NULL,
    foreman_pin  TEXT NOT NULL,
    work_date    TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

-- Выпуск бригады за день, как его ввёл бригадир. Повторный ввод заменяет прежний.
CREATE TABLE IF NOT EXISTS outputs (
    work_date    TEXT NOT NULL,
    brigade_id   TEXT NOT NULL,
    product      TEXT NOT NULL,
    qty          INTEGER NOT NULL,
    foreman_pin  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (work_date, brigade_id, product)
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def since(seconds: int) -> str:
    return (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def touch_device(self, sn: str) -> None:
        ts = now()
        self.conn.execute(
            "INSERT INTO devices (sn, first_seen, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(sn) DO UPDATE SET last_seen = excluded.last_seen",
            (sn, ts, ts),
        )
        self.conn.commit()

    def set_device_info(self, sn: str, info: str) -> None:
        self.conn.execute("UPDATE devices SET info = ? WHERE sn = ?", (info, sn))
        self.conn.commit()

    def attlog_stamp(self, sn: str) -> str | None:
        row = self.conn.execute("SELECT attlog_stamp FROM devices WHERE sn = ?", (sn,)).fetchone()
        return row["attlog_stamp"] if row else None

    def set_attlog_stamp(self, sn: str, stamp: str) -> None:
        self.conn.execute("UPDATE devices SET attlog_stamp = ? WHERE sn = ?", (stamp, sn))
        self.conn.commit()

    def add_punches(self, sn: str, punches: list[dict]) -> int:
        """Сохраняет отметки, повторы (тот же терминал, человек и время) пропускает. Возвращает число новых."""
        ts = now()
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO punches (device_sn, pin, punched_at, status, verify, received_at, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(sn, p["pin"], p["punched_at"], p["status"], p["verify"], ts, p["raw"]) for p in punches],
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def punches(self, date_from: str, date_to: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT device_sn, pin, punched_at, status, verify FROM punches "
            "WHERE punched_at >= ? AND punched_at < ? ORDER BY punched_at",
            (date_from, date_to),
        ).fetchall()
        return [dict(r) for r in rows]

    def queue_command(self, sn: str, command: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO commands (device_sn, command, created_at) VALUES (?, ?, ?)", (sn, command, now())
        )
        self.conn.commit()
        return cur.lastrowid

    def take_pending_commands(self, sn: str) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT id, command FROM commands WHERE device_sn = ? AND sent_at IS NULL ORDER BY id", (sn,)
        ).fetchall()
        if rows:
            self.conn.executemany("UPDATE commands SET sent_at = ? WHERE id = ?", [(now(), r["id"]) for r in rows])
            self.conn.commit()
        return rows

    def finish_command(self, command_id: int, return_code: int) -> None:
        self.conn.execute(
            "UPDATE commands SET done_at = ?, return_code = ? WHERE id = ?", (now(), return_code, command_id)
        )
        self.conn.commit()

    def command(self, command_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM commands WHERE id = ?", (command_id,)).fetchone()
        return dict(row) if row else None

    def last_punch_id(self, sn: str) -> int:
        row = self.conn.execute("SELECT MAX(id) AS id FROM punches WHERE device_sn = ?", (sn,)).fetchone()
        return row["id"] or 0

    def fresh_punches(self, sn: str, after_id: int, max_age_seconds: int) -> list[dict]:
        """Отметки терминала новее after_id, пришедшие недавно. Старые пачки после обрыва связи планшет не будят."""
        rows = self.conn.execute(
            "SELECT id, pin, punched_at, status FROM punches WHERE device_sn = ? AND id > ? AND received_at >= ? "
            "ORDER BY id",
            (sn, after_id, since(max_age_seconds)),
        ).fetchall()
        return [dict(r) for r in rows]

    def open_tablet_session(
        self, token: str, punch_id: int, sn: str, brigade_id: str, foreman_pin: str, work_date: str, ttl: int
    ) -> str:
        """Один сеанс на отметку: если планшет спросил дважды, вернётся уже открытый."""
        expires = (datetime.now() + timedelta(seconds=ttl)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT OR IGNORE INTO tablet_sessions (token, punch_id, device_sn, brigade_id, foreman_pin, work_date, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token, punch_id, sn, brigade_id, foreman_pin, work_date, expires),
        )
        self.conn.commit()
        return self.conn.execute("SELECT token FROM tablet_sessions WHERE punch_id = ?", (punch_id,)).fetchone()[0]

    def tablet_session(self, token: str, sn: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tablet_sessions WHERE token = ? AND device_sn = ? AND expires_at > ?", (token, sn, now())
        ).fetchone()
        return dict(row) if row else None

    def save_output(self, work_date: str, brigade_id: str, foreman_pin: str, qty: dict[str, int]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM outputs WHERE work_date = ? AND brigade_id = ?", (work_date, brigade_id))
            self.conn.executemany(
                "INSERT INTO outputs (work_date, brigade_id, product, qty, foreman_pin, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(work_date, brigade_id, name, n, foreman_pin, now()) for name, n in qty.items() if n > 0],
            )

    def outputs(self, work_date: str, brigade_id: str | None = None) -> list[dict]:
        sql = "SELECT work_date, brigade_id, product, qty, foreman_pin, updated_at FROM outputs WHERE work_date = ?"
        args = [work_date]
        if brigade_id:
            sql += " AND brigade_id = ?"
            args.append(brigade_id)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY brigade_id, rowid", args).fetchall()]
