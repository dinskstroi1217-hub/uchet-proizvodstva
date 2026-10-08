"""Хранилище SQLite: терминалы, отметки и очередь команд для терминалов."""

import sqlite3
from datetime import datetime
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
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
