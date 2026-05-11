"""Per-user strategy and risk/cost defaults for backtesting forms."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path


class UserDefaultsStore:
    """Per-user strategy and risk/cost defaults for backtesting.

    Stores preferred strategy parameters and risk settings so backtest
    forms can pre-fill with saved values across sessions.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_schema()

    def _apply_schema(self) -> None:
        schema_sql = (
            resources.files("aurex_trade.adapters.sqlite")
            .joinpath("schema.sql")
            .read_text(encoding="utf-8")
        )
        self._conn.executescript(schema_sql)

    def close(self) -> None:
        self._conn.close()

    # --- Strategy Defaults ---

    def save_strategy_defaults(
        self,
        user_id: str,
        strategy_name: str,
        params: dict[str, int | float],
        *,
        is_preferred: bool = False,
    ) -> None:
        """Upsert strategy params for a user.

        If is_preferred is True, clears preferred flag on other strategies first.
        """
        now = datetime.now(tz=UTC).isoformat()
        if is_preferred:
            self._conn.execute(
                "UPDATE user_strategy_defaults SET is_preferred = 0 WHERE user_id = ?",
                (user_id,),
            )
        self._conn.execute(
            """
            INSERT INTO user_strategy_defaults
                (user_id, strategy_name, params_json, is_preferred, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, strategy_name) DO UPDATE SET
                params_json = excluded.params_json,
                is_preferred = excluded.is_preferred,
                updated_at = excluded.updated_at
            """,
            (user_id, strategy_name, json.dumps(params), int(is_preferred), now),
        )
        self._conn.commit()

    def get_strategy_defaults(
        self, user_id: str, strategy_name: str
    ) -> dict[str, int | float] | None:
        """Return saved params for a specific strategy, or None."""
        cursor = self._conn.execute(
            "SELECT params_json FROM user_strategy_defaults"
            " WHERE user_id = ? AND strategy_name = ?",
            (user_id, strategy_name),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        result: dict[str, int | float] = json.loads(row["params_json"])
        return result

    def get_preferred_strategy(self, user_id: str) -> str | None:
        """Return the name of the user's preferred strategy, or None."""
        cursor = self._conn.execute(
            "SELECT strategy_name FROM user_strategy_defaults"
            " WHERE user_id = ? AND is_preferred = 1",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        result: str = row["strategy_name"]
        return result

    def get_all_strategy_defaults(
        self, user_id: str
    ) -> dict[str, dict[str, int | float]]:
        """Return all saved strategy params as {strategy_name: params_dict}."""
        cursor = self._conn.execute(
            "SELECT strategy_name, params_json FROM user_strategy_defaults WHERE user_id = ?",
            (user_id,),
        )
        result: dict[str, dict[str, int | float]] = {}
        for row in cursor.fetchall():
            result[row["strategy_name"]] = json.loads(row["params_json"])
        return result

    def delete_strategy_defaults(self, user_id: str, strategy_name: str) -> None:
        """Remove saved params for a strategy (revert to app defaults)."""
        self._conn.execute(
            "DELETE FROM user_strategy_defaults WHERE user_id = ? AND strategy_name = ?",
            (user_id, strategy_name),
        )
        self._conn.commit()

    # --- Risk/Cost Defaults ---

    def save_risk_defaults(
        self, user_id: str, settings: dict[str, int | float | bool | str]
    ) -> None:
        """Upsert the user's risk and cost settings."""
        now = datetime.now(tz=UTC).isoformat()
        self._conn.execute(
            """
            INSERT INTO user_risk_defaults (user_id, settings_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                settings_json = excluded.settings_json,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(settings), now),
        )
        self._conn.commit()

    def get_risk_defaults(self, user_id: str) -> dict[str, int | float | bool | str] | None:
        """Return saved risk/cost settings, or None if not set."""
        cursor = self._conn.execute(
            "SELECT settings_json FROM user_risk_defaults WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        result: dict[str, int | float | bool | str] = json.loads(row["settings_json"])
        return result

    def delete_risk_defaults(self, user_id: str) -> None:
        """Remove saved risk settings (revert to app defaults)."""
        self._conn.execute(
            "DELETE FROM user_risk_defaults WHERE user_id = ?",
            (user_id,),
        )
        self._conn.commit()
