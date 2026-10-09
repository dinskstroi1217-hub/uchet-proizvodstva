"""Справочник: изделия, сотрудники и бригады. Пока лежит в JSON-файле, позже его будет выгружать 1С.

Пример формата — catalog.example.json в корне репозитория. Настоящий файл с фамилиями в репозиторий не кладём.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Brigade:
    id: str
    name: str
    foreman_pin: str
    member_pins: tuple[str, ...]
    # Группы изделий, которые делает бригада. Бригадир на планшете видит только их.
    groups: tuple[str, ...]


class Catalog:
    def __init__(self, data: dict):
        self.products: list[dict] = [{"name": p["name"], "group": p["group"]} for p in data.get("products", [])]
        self.employees: dict[str, str] = {str(e["pin"]): e["name"] for e in data.get("employees", [])}
        self.brigades: list[Brigade] = [
            Brigade(
                id=b["id"],
                name=b["name"],
                foreman_pin=str(b["foreman_pin"]),
                member_pins=tuple(str(p) for p in b.get("member_pins", [])),
                groups=tuple(b["groups"]),
            )
            for b in data.get("brigades", [])
        ]

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        path = Path(path)
        if not path.exists():
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def brigade_of_foreman(self, pin: str) -> Brigade | None:
        return next((b for b in self.brigades if b.foreman_pin == pin), None)

    def brigade(self, brigade_id: str) -> Brigade | None:
        return next((b for b in self.brigades if b.id == brigade_id), None)

    def products_of(self, brigade: Brigade) -> list[dict]:
        """Изделия бригады в порядке её групп, а внутри группы — как в справочнике."""
        return [p for group in brigade.groups for p in self.products if p["group"] == group]
