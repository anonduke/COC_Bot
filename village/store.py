"""SQLite store for village snapshots.

One file (data/village.db by default), WAL mode so the dashboard can read
while the orchestrator writes. Later phases add their tables here (plan,
events, sessions, journal, notifications) as new schema versions.
"""
import json
import os
import sqlite3
import time
from dataclasses import dataclass

from .export import parse_export

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "village.db")

_MIGRATIONS = [
    # v1
    """
    CREATE TABLE snapshots (
        id           INTEGER PRIMARY KEY,
        tag          TEXT    NOT NULL,
        export_ts    INTEGER NOT NULL,   -- the export's own timestamp (epoch s)
        captured_at  INTEGER NOT NULL,   -- when the bot read it (epoch s)
        game_version TEXT,
        gold         INTEGER,            -- storage OCR at capture time; NULL = not read
        elixir       INTEGER,
        dark_elixir  INTEGER,
        gems         INTEGER,
        raw_json     TEXT    NOT NULL,
        UNIQUE (tag, export_ts)
    );
    CREATE INDEX snapshots_tag_ts ON snapshots (tag, export_ts);
    """,
]


@dataclass(frozen=True)
class Resources:
    gold: int | None = None
    elixir: int | None = None
    dark_elixir: int | None = None
    gems: int | None = None


@dataclass
class StoredSnapshot:
    id: int
    captured_at: int
    game_version: str | None
    resources: Resources
    snapshot: object  # export.Snapshot


class Store:
    def __init__(self, path=DEFAULT_PATH):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self):
        self.db.close()

    def _migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
            # One transaction per step: the schema and its version move together.
            self.db.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {i};\nCOMMIT;")

    def save_snapshot(self, snapshot, resources=None, game_version=None, captured_at=None):
        """Store a snapshot; returns its id. Saving the same export twice
        (same tag and timestamp) returns the existing id."""
        r = resources or Resources()
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO snapshots (tag, export_ts, captured_at, game_version,"
                " gold, elixir, dark_elixir, gems, raw_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (snapshot.tag, snapshot.timestamp,
                 int(captured_at if captured_at is not None else time.time()), game_version,
                 r.gold, r.elixir, r.dark_elixir, r.gems,
                 json.dumps(snapshot.raw, separators=(",", ":"))))
            if cur.rowcount:
                return cur.lastrowid
        return self.db.execute("SELECT id FROM snapshots WHERE tag=? AND export_ts=?",
                               (snapshot.tag, snapshot.timestamp)).fetchone()[0]

    def _load(self, row):
        if row is None:
            return None
        return StoredSnapshot(
            id=row["id"], captured_at=row["captured_at"], game_version=row["game_version"],
            resources=Resources(row["gold"], row["elixir"], row["dark_elixir"], row["gems"]),
            snapshot=parse_export(row["raw_json"]))

    def get(self, snapshot_id):
        return self._load(self.db.execute("SELECT * FROM snapshots WHERE id=?",
                                          (snapshot_id,)).fetchone())

    def latest(self, tag=None, before_id=None):
        """Newest snapshot by export time, optionally for one tag and/or older
        than a given snapshot id (to fetch "the previous one")."""
        sql, args = "SELECT * FROM snapshots WHERE 1=1", []
        if tag is not None:
            sql += " AND tag=?"
            args.append(tag)
        if before_id is not None:
            sql += " AND export_ts < (SELECT export_ts FROM snapshots WHERE id=?)"
            args.append(before_id)
        sql += " ORDER BY export_ts DESC, id DESC LIMIT 1"
        return self._load(self.db.execute(sql, args).fetchone())

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
