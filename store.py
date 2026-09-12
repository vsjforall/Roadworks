"""
Storage. SQLite to start, Postgres when you have a second reader.

The important part is not the engine, it is that `upsert` advances a record
rather than inserting a duplicate. A tender first seen as TENDERED in June and
awarded in September is one row with a changing status, because the app's core
promise is that an old tender re-surfaces the day its winner appears.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime

from .schema import TenderRecord, Status

DDL = """
CREATE TABLE IF NOT EXISTS tenders (
  tender_id TEXT NOT NULL,
  source TEXT NOT NULL,
  authority TEXT,
  work_title TEXT,
  source_url TEXT,
  work_type TEXT,
  road_name TEXT,
  stretch TEXT,
  chainage_from REAL,
  chainage_to REAL,
  districts TEXT,
  estimated_cost INTEGER,
  emd INTEGER,
  status TEXT,
  winner_name TEXT,
  winner_value INTEGER,
  awarded_on TEXT,
  display_value TEXT,
  display_variance TEXT,
  confidence REAL,
  first_seen TEXT,
  last_seen TEXT,
  fingerprint TEXT,
  PRIMARY KEY (tender_id, source)
);
CREATE INDEX IF NOT EXISTS ix_status  ON tenders(status, last_seen DESC);
CREATE INDEX IF NOT EXISTS ix_road    ON tenders(road_name);
CREATE INDEX IF NOT EXISTS ix_awarded ON tenders(awarded_on DESC);
"""

# Never let a record slide backwards. NIC list pages sometimes re-show an
# awarded tender in the live list after an amendment; without this guard the
# app would tell a user a confirmed award had reverted to open bidding.
ORDER = {
    Status.ANNOUNCED: 0, Status.TENDERED: 1, Status.BIDS_OPENED: 2,
    Status.AWARDED: 3, Status.IN_PROGRESS: 4, Status.COMPLETED: 5,
    Status.CANCELLED: 6,
}


class Store:
    """
    FastAPI runs sync handlers on a threadpool, and SQLite connections are not
    shareable across threads. One connection per thread, created on demand.
    """

    def __init__(self, path: str = "kerala_infra.db"):
        self.path = path
        self._local = threading.local()
        self.db.executescript(DDL)
        self.db.commit()

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def seen(self, tender_id: str, fingerprint: str) -> bool:
        row = self.db.execute(
            "SELECT fingerprint FROM tenders WHERE tender_id = ?", (tender_id,)
        ).fetchone()
        return bool(row and row["fingerprint"] == fingerprint)

    def upsert(self, rec: TenderRecord, fingerprint: str) -> None:
        d = asdict(rec)
        d["districts"] = json.dumps(rec.districts)
        d["work_type"] = rec.work_type.value
        d["status"] = rec.status.value
        for k in ("awarded_on", "first_seen", "last_seen",
                  "bid_due_date", "bid_open_date"):
            v = d.get(k)
            d[k] = v.isoformat() if isinstance(v, datetime) else v
        d["fingerprint"] = fingerprint
        d.setdefault("display_value", None)
        d.setdefault("display_variance", None)

        prev = self.db.execute(
            "SELECT status, first_seen FROM tenders "
            "WHERE tender_id = ? AND source = ?",
            (rec.tender_id, rec.source),
        ).fetchone()

        if prev:
            old = Status(prev["status"])
            if ORDER[old] > ORDER[rec.status]:
                d["status"] = old.value
            d["first_seen"] = prev["first_seen"]

        cols = [c for c in d if c in
                {r[1] for r in self.db.execute("PRAGMA table_info(tenders)")}]
        self.db.execute(
            f"INSERT OR REPLACE INTO tenders ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [d[c] for c in cols],
        )
        self.db.commit()

    # confidence >= 0.7 is the review floor from extract.py. Anything the
    # model was unsure about never reaches a user's feed.
    BASE = ("SELECT * FROM tenders WHERE confidence >= 0.7 "
            "AND status IN ('awarded','bids_opened')")

    def query(self, kind: str = "all", district: str | None = None,
              limit: int = 60) -> list[dict]:
        q, args = self.BASE, []
        if kind == "nh":
            q += " AND road_name LIKE 'NH%'"
        elif kind == "bridge":
            q += " AND work_type IN ('bridge','rob','rub','flyover','culvert')"
        elif kind == "big":
            q += " AND COALESCE(winner_value, estimated_cost) >= 100000000"
        elif kind == "awarded":
            q += " AND status = 'awarded'"
        if district:
            q += " AND districts LIKE ?"
            args.append(f'%"{district}"%')
        q += " ORDER BY COALESCE(awarded_on, last_seen) DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self.db.execute(q, args)]

    def get(self, tender_id: str) -> dict | None:
        r = self.db.execute(
            "SELECT * FROM tenders WHERE tender_id = ?", (tender_id,)
        ).fetchone()
        return self._row(r) if r else None

    def district_counts(self) -> list[dict]:
        out = []
        for d in self.db.execute(self.BASE):
            for name in json.loads(d["districts"] or "[]"):
                out.append(name)
        return [{"district": n, "count": out.count(n)}
                for n in sorted(set(out))]

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM tenders").fetchone()["c"]

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["districts"] = json.loads(d.get("districts") or "[]")
        d.pop("fingerprint", None)
        return d
