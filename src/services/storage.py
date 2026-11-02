import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, database_path: str | Path) -> None: self.database_path = Path(database_path); self.database_path.parent.mkdir(parents=True, exist_ok=True); self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path); connection.row_factory = sqlite3.Row; return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS artifacthub_packages (package TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS release_notes (package TEXT PRIMARY KEY, folder TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            connection.execute("""CREATE TABLE IF NOT EXISTS upgrade_guides (package TEXT NOT NULL, previous_version TEXT NOT NULL, latest_version TEXT NOT NULL, guide TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (package, previous_version, latest_version))""")

    @staticmethod
    def _json_dumps(data: Any) -> str: return json.dumps(data, ensure_ascii=False, default=str)

    def save_artifacthub_package(self, package: str, data: dict) -> None:
        with self.connect() as connection:
            connection.execute("""INSERT INTO artifacthub_packages (package, data, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(package) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""", (package, self._json_dumps(data)))

    def get_artifacthub_packages(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""SELECT data FROM artifacthub_packages ORDER BY package""").fetchall()
        packages = []
        for row in rows:
            package = json.loads(row["data"]); published = package.get("published")
            if published and isinstance(published, str):
                try: package["published"] = datetime.fromisoformat(published)
                except ValueError: pass
            packages.append(package)
        return packages

    def save_release_notes(self, package: str, folder: str, data: dict) -> None:
        with self.connect() as connection:
            connection.execute("""INSERT INTO release_notes (package, folder, data, updated_at) VALUES (?, ?, ?, datetime('now')) ON CONFLICT(package) DO UPDATE SET folder = excluded.folder, data = excluded.data, updated_at = excluded.updated_at""", (package, folder, self._json_dumps(data)))

    def get_release_notes(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""SELECT package, folder, data, updated_at FROM release_notes ORDER BY package""").fetchall()
        results = []
        for row in rows:
            data = json.loads(row["data"]); data["package"] = row["package"]; data["folder"] = row["folder"]; data["updated_at"] = row["updated_at"]; results.append(data)
        return results

    def save_upgrade_guide(self, package: str, previous_version: str, latest_version: str, guide: str) -> None:
        if not isinstance(guide, str): raise TypeError("guide must be a string")
        if not guide.strip(): raise ValueError("Refusing to save an empty upgrade guide")
        with self.connect() as connection:
            connection.execute("""INSERT INTO upgrade_guides (package, previous_version, latest_version, guide, updated_at) VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(package, previous_version, latest_version) DO UPDATE SET guide = excluded.guide, updated_at = excluded.updated_at""", (package, previous_version, latest_version, guide))

    def get_upgrade_guide(self, package: str, previous_version: str, latest_version: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute("""SELECT guide, updated_at FROM upgrade_guides WHERE package = ? AND previous_version = ? AND latest_version = ?""", (package, previous_version, latest_version)).fetchone()
        if row is None: return None
        return {"guide": row["guide"], "updated_at": row["updated_at"], "previous_version": previous_version, "latest_version": latest_version}
