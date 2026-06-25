"""SQLite-backed persistence layer.

Stores each Claim as a full JSON blob — no schema migration needed when
Pydantic models evolve, and no ORM complexity. A single table:
  claims(id TEXT PK, data TEXT, created_at TEXT, updated_at TEXT)
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("clearprocess.db")


class Database:
    def __init__(self, path: str = str(DB_PATH)):
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS claims (
                    id         TEXT PRIMARY KEY,
                    data       TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_orders (
                    claim_id      TEXT PRIMARY KEY,
                    work_order_no TEXT NOT NULL,
                    data          TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS policies (
                    policy_number TEXT PRIMARY KEY,
                    data          TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                )
            """)
            conn.commit()

    def save(self, claim) -> None:
        data = claim.model_dump_json()
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                (
                    "INSERT OR REPLACE INTO claims "
                    "(id, data, created_at, updated_at) VALUES (?, ?, ?, ?)"
                ),
                (claim.id, data, claim.created_at.isoformat(), datetime.utcnow().isoformat()),
            )
            if claim.work_order is not None and claim.work_order.number:
                self._save_work_order(conn, claim)
            conn.commit()

    def _save_work_order(self, conn: sqlite3.Connection, claim) -> None:
        work_order_payload = {
            "claim_id": claim.id,
            "claimant_name": claim.claimant.name,
            "policy_number": claim.claimant.policy_number,
            "vehicle": {
                "make": claim.vehicle.make,
                "model": claim.vehicle.model,
                "year": claim.vehicle.year,
                "license_plate": claim.vehicle.license_plate,
            },
            "current_stage": claim.current_stage.value,
            "status": claim.status.value,
            "work_order": claim.work_order.model_dump(mode="json"),
        }
        work_order_created_at = claim.work_order.created_at or claim.updated_at or datetime.utcnow()
        conn.execute(
            """
            INSERT OR REPLACE INTO work_orders (
                claim_id, work_order_no, data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                claim.id,
                claim.work_order.number,
                json.dumps(work_order_payload),
                work_order_created_at.isoformat(),
                datetime.utcnow().isoformat(),
            ),
        )

    def get(self, claim_id: str):
        from models.claim import Claim
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT data FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if row:
            return Claim.model_validate_json(row[0])
        return None

    def list_all(self):
        from models.claim import Claim
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT data FROM claims ORDER BY created_at DESC"
            ).fetchall()
        return [Claim.model_validate_json(row[0]) for row in rows]

    def list_work_orders(self):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT data FROM work_orders ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_policy(self, policy_number: str, data: dict) -> None:
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.path) as conn:
            existing = conn.execute(
                "SELECT created_at FROM policies WHERE policy_number = ?",
                (policy_number,),
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                (
                    "INSERT OR REPLACE INTO policies "
                    "(policy_number, data, created_at, updated_at) VALUES (?, ?, ?, ?)"
                ),
                (policy_number, json.dumps(data), created_at, now),
            )
            conn.commit()

    def get_policy(self, policy_number: str) -> dict | None:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute(
                "SELECT data FROM policies WHERE policy_number = ?",
                (policy_number,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_policies(self) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT data FROM policies ORDER BY updated_at DESC"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]


# Module-level singleton
db = Database()
