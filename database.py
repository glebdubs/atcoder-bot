import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(__file__).parent / "bot_data.db"


@dataclass
class GuildConfig:
    guild_id: int
    channel_id: int
    role_id: int
    updated_at: int


class Database:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_configs (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS announced_contests (
                    guild_id INTEGER NOT NULL,
                    contest_key TEXT NOT NULL,
                    announced_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, contest_key)
                )
                """
            )
            conn.commit()

    def set_guild_config(self, guild_id: int, channel_id: int, role_id: int) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guild_configs (guild_id, channel_id, role_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    role_id = excluded.role_id,
                    updated_at = excluded.updated_at
                """,
                (guild_id, channel_id, role_id, now),
            )
            conn.commit()

    def remove_guild_config(self, guild_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM guild_configs WHERE guild_id = ?", (guild_id,)
            )
            conn.commit()
            return cur.rowcount > 0

    def get_guild_config(self, guild_id: int) -> Optional[GuildConfig]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT guild_id, channel_id, role_id, updated_at FROM guild_configs WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            if not row:
                return None
            return GuildConfig(
                guild_id=row["guild_id"],
                channel_id=row["channel_id"],
                role_id=row["role_id"],
                updated_at=row["updated_at"],
            )

    def get_all_guild_configs(self) -> List[GuildConfig]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT guild_id, channel_id, role_id, updated_at FROM guild_configs"
            ).fetchall()
            return [
                GuildConfig(
                    guild_id=r["guild_id"],
                    channel_id=r["channel_id"],
                    role_id=r["role_id"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    def has_announced(self, guild_id: int, contest_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM announced_contests WHERE guild_id = ? AND contest_key = ?",
                (guild_id, contest_key),
            ).fetchone()
            return row is not None

    def mark_announced(self, guild_id: int, contest_key: str) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO announced_contests (guild_id, contest_key, announced_at)
                VALUES (?, ?, ?)
                """,
                (guild_id, contest_key, now),
            )
            conn.commit()
