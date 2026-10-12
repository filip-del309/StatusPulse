import json
import sqlite3

from pathlib import Path
from datetime import datetime


class Storage:
    def __init__(
        self,
        database_path: str,
    ):
        self.database_path = Path(
            database_path
        )

        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.initialize()

    def connect(self):
        connection = sqlite3.connect(
            self.database_path
        )

        connection.row_factory = sqlite3.Row

        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacthub_packages (
                    package TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def save_artifacthub_package(
        self,
        package: str,
        data: dict,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacthub_packages (
                    package,
                    data,
                    updated_at
                )
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(package)
                DO UPDATE SET
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    package,
                    json.dumps(
                        data,
                        default=str,
                    ),
                ),
            )

    def get_artifacthub_packages(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT data
                FROM artifacthub_packages
                ORDER BY package
            """).fetchall()

        packages = []

        for row in rows:
            package = json.loads(row["data"])

            if package.get("published"):
                package["published"] = datetime.fromisoformat(
                    package["published"]
                )

            packages.append(package)

        return packages
