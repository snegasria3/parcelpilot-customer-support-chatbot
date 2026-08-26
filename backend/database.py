from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    account_id TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL,
    csm TEXT NOT NULL,
    contract_file TEXT,
    premium_support INTEGER NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    carrier TEXT NOT NULL,
    status TEXT NOT NULL,
    booked_at TEXT,
    pickup_window_start TEXT,
    pickup_window_end TEXT,
    pickup_actual_at TEXT,
    shipment_fee_inr REAL NOT NULL,
    carrier_fault INTEGER NOT NULL,
    customer_fault INTEGER NOT NULL,
    cancellation_requested_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, order_id);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    channel TEXT,
    assigned_to TEXT,
    last_customer_message_at TEXT,
    historical_resolution TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_account ON tickets(account_id, ticket_id);

CREATE TABLE IF NOT EXISTS pending_actions (
    action_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    target_id TEXT,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    confirmed_at TEXT,
    cancelled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_scope ON pending_actions(account_id, user_id, status);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row is not None else None

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self.connect() as connection:
            connection.execute(sql, parameters)

    def scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        row = self.fetch_one(sql, parameters)
        return next(iter(row.values())) if row else None

    def metadata(self, key: str) -> str | None:
        row = self.fetch_one("SELECT value FROM metadata WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    def scoped_account(self, account_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM accounts WHERE account_id = ? AND status = 'active'", (account_id,))

    def scoped_order(self, account_id: str, order_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT * FROM orders WHERE account_id = ? AND upper(order_id) = upper(?)",
            (account_id, order_id),
        )

    def scoped_ticket(self, account_id: str, ticket_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT * FROM tickets WHERE account_id = ? AND upper(ticket_id) = upper(?)",
            (account_id, ticket_id),
        )

    def audit(
        self,
        *,
        trace_id: str,
        account_id: str,
        user_id: str,
        event_type: str,
        outcome: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        self.execute(
            """INSERT INTO audit_log
            (trace_id, account_id, user_id, event_type, outcome, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id,
                account_id,
                user_id,
                event_type,
                outcome,
                json.dumps(metadata, separators=(",", ":")),
                created_at,
            ),
        )
