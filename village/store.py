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
    # v2: the home-screen builder counter "free/total" at capture time
    """
    ALTER TABLE snapshots ADD COLUMN builders_free INTEGER;
    ALTER TABLE snapshots ADD COLUMN builders_total INTEGER;
    """,
    # v3: dashboard: settings, the upgrade queue, notifications
    """
    CREATE TABLE settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,         -- JSON
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE plan_items (
        id           INTEGER PRIMARY KEY,
        position     INTEGER NOT NULL,     -- queue order, 0 first
        data_id      INTEGER NOT NULL,     -- export ID of the building/hero/trap type
        target_level INTEGER,              -- NULL = max for the current Town Hall
        enabled      INTEGER NOT NULL DEFAULT 1,
        note         TEXT NOT NULL DEFAULT '',
        created_at   INTEGER NOT NULL
    );
    CREATE TABLE notifications (
        id         INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        severity   TEXT NOT NULL,          -- info, warning, error
        source     TEXT NOT NULL,          -- capture, web, ...
        message    TEXT NOT NULL,
        detail     TEXT NOT NULL DEFAULT '',
        acked_at   INTEGER
    );
    CREATE INDEX notifications_open ON notifications (acked_at, created_at);
    """,
    # v4: farming runs started from the dashboard
    """
    CREATE TABLE farm_runs (
        id             INTEGER PRIMARY KEY,
        created_at     INTEGER NOT NULL,
        started_at     INTEGER,
        ended_at       INTEGER,
        heartbeat_at   INTEGER,
        status         TEXT NOT NULL,      -- queued, running, done, stopped, failed
        params         TEXT NOT NULL,      -- JSON
        attacks        INTEGER NOT NULL DEFAULT 0,
        fails          INTEGER NOT NULL DEFAULT 0,
        walls          INTEGER NOT NULL DEFAULT 0,
        message        TEXT NOT NULL DEFAULT '',
        start_state    TEXT,               -- JSON: resources + bar fills at the start
        state          TEXT,               -- JSON: latest resources + bar fills
        stop_requested INTEGER NOT NULL DEFAULT 0,
        pid            INTEGER
    );
    """,
]
SEVERITIES = ("info", "warning", "error")


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
    counter: tuple | None = None  # (free, total) read from the screen


class Store:
    def __init__(self, path=DEFAULT_PATH):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # timeout: the capture process and the dashboard write concurrently.
        self.db = sqlite3.connect(path, timeout=10)
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

    def save_snapshot(self, snapshot, resources=None, game_version=None, captured_at=None,
                      counter=None):
        """Store a snapshot; returns its id. Saving the same export twice
        (same tag and timestamp) returns the existing id."""
        r = resources or Resources()
        free, total = counter if counter else (None, None)
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO snapshots (tag, export_ts, captured_at, game_version,"
                " gold, elixir, dark_elixir, gems, raw_json, builders_free, builders_total)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (snapshot.tag, snapshot.timestamp,
                 int(captured_at if captured_at is not None else time.time()), game_version,
                 r.gold, r.elixir, r.dark_elixir, r.gems,
                 json.dumps(snapshot.raw, separators=(",", ":")), free, total))
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
            snapshot=parse_export(row["raw_json"]),
            counter=((row["builders_free"], row["builders_total"])
                     if row["builders_total"] is not None else None))

    def all(self, tag=None, since=None):
        """All snapshots (optionally one tag, export time >= since), oldest first."""
        sql, args = "SELECT * FROM snapshots WHERE 1=1", []
        if tag is not None:
            sql += " AND tag=?"
            args.append(tag)
        if since is not None:
            sql += " AND export_ts>=?"
            args.append(since)
        sql += " ORDER BY export_ts, id"
        return [self._load(r) for r in self.db.execute(sql, args).fetchall()]

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

    def resource_history(self, since):
        """[(export_ts, gold, elixir, dark_elixir, gems, builders_free, builders_total)]."""
        return [tuple(r) for r in self.db.execute(
            "SELECT export_ts, gold, elixir, dark_elixir, gems, builders_free, builders_total "
            "FROM snapshots WHERE export_ts >= ? ORDER BY export_ts", (since,))]

    # --- settings ---------------------------------------------------------
    def settings(self):
        """{key: value} of every stored setting (defaults live in settings.py)."""
        return {r["key"]: json.loads(r["value"])
                for r in self.db.execute("SELECT key, value FROM settings")}

    def set_setting(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), int(time.time())))

    # --- upgrade queue ----------------------------------------------------
    def plan(self):
        return [dict(r) | {"enabled": bool(r["enabled"])} for r in self.db.execute(
            "SELECT id, position, data_id, target_level, enabled, note, created_at "
            "FROM plan_items ORDER BY position, id")]

    def add_plan_item(self, data_id, target_level=None, note=""):
        with self.db:
            pos = self.db.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM plan_items").fetchone()[0]
            return self.db.execute(
                "INSERT INTO plan_items (position, data_id, target_level, note, created_at) "
                "VALUES (?,?,?,?,?)", (pos, data_id, target_level, note, int(time.time()))).lastrowid

    def update_plan_item(self, item_id, **fields):
        allowed = {"target_level", "enabled", "note"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        if not fields:
            return False
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.db:
            cur = self.db.execute(f"UPDATE plan_items SET {cols} WHERE id=?",
                                  (*fields.values(), item_id))
        return cur.rowcount == 1

    def delete_plan_item(self, item_id):
        with self.db:
            return self.db.execute("DELETE FROM plan_items WHERE id=?", (item_id,)).rowcount == 1

    def reorder_plan(self, ids):
        """Set the queue order; `ids` must be exactly the current item ids."""
        current = {r[0] for r in self.db.execute("SELECT id FROM plan_items")}
        if set(ids) != current or len(ids) != len(current):
            raise ValueError("order must list every queue item exactly once")
        with self.db:
            self.db.executemany("UPDATE plan_items SET position=? WHERE id=?",
                                [(pos, i) for pos, i in enumerate(ids)])

    # --- notifications ----------------------------------------------------
    def notify(self, severity, source, message, detail=""):
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        with self.db:
            return self.db.execute(
                "INSERT INTO notifications (created_at, severity, source, message, detail) "
                "VALUES (?,?,?,?,?)", (int(time.time()), severity, source, message, detail)).lastrowid

    def notifications(self, limit=50, include_acked=True):
        sql = "SELECT * FROM notifications"
        if not include_acked:
            sql += " WHERE acked_at IS NULL"
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        return [dict(r) for r in self.db.execute(sql, (limit,))]

    # --- farming runs -----------------------------------------------------
    _FARM_FIELDS = {"started_at", "ended_at", "heartbeat_at", "status", "attacks", "fails",
                    "walls", "message", "start_state", "state", "stop_requested", "pid"}

    def create_farm_run(self, params):
        with self.db:
            return self.db.execute(
                "INSERT INTO farm_runs (created_at, status, params) VALUES (?, 'queued', ?)",
                (int(time.time()), json.dumps(params))).lastrowid

    def update_farm_run(self, run_id, **fields):
        bad = set(fields) - self._FARM_FIELDS
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        for k in ("start_state", "state"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.db:
            self.db.execute(f"UPDATE farm_runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def farm_run(self, run_id):
        r = self.db.execute("SELECT * FROM farm_runs WHERE id=?", (run_id,)).fetchone()
        return self._farm_dict(r)

    def farm_runs(self, limit=20):
        return [self._farm_dict(r) for r in self.db.execute(
            "SELECT * FROM farm_runs ORDER BY id DESC LIMIT ?", (limit,))]

    @staticmethod
    def _farm_dict(r):
        if r is None:
            return None
        d = dict(r)
        for k in ("params", "start_state", "state"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["stop_requested"] = bool(d["stop_requested"])
        return d

    def unread_count(self):
        return self.db.execute("SELECT COUNT(*) FROM notifications WHERE acked_at IS NULL").fetchone()[0]

    def ack(self, notification_id=None):
        """Acknowledge one notification, or all when id is None."""
        with self.db:
            if notification_id is None:
                return self.db.execute("UPDATE notifications SET acked_at=? WHERE acked_at IS NULL",
                                       (int(time.time()),)).rowcount
            return self.db.execute("UPDATE notifications SET acked_at=? WHERE id=? AND acked_at IS NULL",
                                   (int(time.time()), notification_id)).rowcount
