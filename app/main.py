"""Точка входа сервиса: uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8080"""

import logging
import os
from dataclasses import dataclass, field

from fastapi import FastAPI

from app import adms, api, tablet
from app.catalog import Catalog
from app.db import Database


@dataclass
class Settings:
    db_path: str = "uchet.sqlite3"
    api_token: str = ""
    # Серийные номера терминалов, которым разрешено присылать данные. Пусто — принимать от всех (только для отладки).
    allowed_serials: set[str] = field(default_factory=set)
    timezone_hours: int = 3
    poll_delay: int = 10
    catalog_path: str = "catalog.json"
    # Ключ в адресе страницы планшета. Пусто — планшет выключен.
    tablet_key: str = ""
    # Сколько секунд после отметки бригадир может вводить выпуск.
    tablet_session_seconds: int = 600
    # Отметки старше этого планшет не показывает (например, пачка после обрыва связи).
    tablet_fresh_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        serials = os.environ.get("ALLOWED_SERIALS", "")
        return cls(
            db_path=os.environ.get("DB_PATH", cls.db_path),
            api_token=os.environ.get("API_TOKEN", ""),
            allowed_serials={s.strip() for s in serials.split(",") if s.strip()},
            timezone_hours=int(os.environ.get("TIMEZONE_HOURS", cls.timezone_hours)),
            poll_delay=int(os.environ.get("POLL_DELAY", cls.poll_delay)),
            catalog_path=os.environ.get("CATALOG_PATH", cls.catalog_path),
            tablet_key=os.environ.get("TABLET_KEY", ""),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = settings or Settings.from_env()
    app = FastAPI(title="Учёт производства")
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.catalog = Catalog.load(settings.catalog_path)
    app.include_router(adms.router)
    app.include_router(api.router)
    app.include_router(tablet.router)
    return app

