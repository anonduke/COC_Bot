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
    # v5: orchestrator: sessions, the action journal, commands, runtime status
    """
    CREATE TABLE sessions (
        id          INTEGER PRIMARY KEY,
        trigger     TEXT NOT NULL,          -- builder_free, health_check, manual, retry
        started_at  INTEGER NOT NULL,
        ended_at    INTEGER,
        status      TEXT NOT NULL,          -- running, done, failed, interrupted
        summary     TEXT NOT NULL DEFAULT '',
        pid         INTEGER
    );
    CREATE TABLE actions (
        id              INTEGER PRIMARY KEY,
        session_id      INTEGER REFERENCES sessions(id),
        created_at      INTEGER NOT NULL,
        kind            TEXT NOT NULL,      -- upgrade, dry_run, farm
        data_id         INTEGER,
        name            TEXT NOT NULL DEFAULT '',
        from_level      INTEGER,
        before_snapshot INTEGER,
        status          TEXT NOT NULL,      -- intent, verified, not_applied, skipped, dry_run, failed
        detail          TEXT NOT NULL DEFAULT '',
        updated_at      INTEGER
    );
    CREATE INDEX actions_status ON actions (status);
    CREATE TABLE commands (
        id         INTEGER PRIMARY KEY,
        created_at INTEGER NOT NULL,
        kind       TEXT NOT NULL,           -- run_session
        done_at    INTEGER,
        result     TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE kv (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,           -- JSON
        updated_at INTEGER NOT NULL
    );
    """,
    # v6: several game accounts. Everything per village carries its tag; the
    # active one is the player_tag setting. Rows from before are tagged by
    # _adopt_untagged() with the account that was in use.
    """
    CREATE TABLE accounts (
        tag          TEXT PRIMARY KEY,
        scid         TEXT,                  -- name on its card in the Supercell ID switch list
        village      TEXT,                  -- village name in the game
        display_name TEXT,                  -- user override for the dashboard
        town_hall    INTEGER,
        position     INTEGER,               -- order in the switch list
        added_at     INTEGER NOT NULL,
        switched_at  INTEGER
    );
    ALTER TABLE plan_items ADD COLUMN tag TEXT;
    ALTER TABLE sessions ADD COLUMN tag TEXT;
    ALTER TABLE actions ADD COLUMN tag TEXT;
    ALTER TABLE farm_runs ADD COLUMN tag TEXT;
    ALTER TABLE notifications ADD COLUMN tag TEXT;
    CREATE INDEX plan_items_tag ON plan_items (tag, position);
    """,
]
ACTIVE = object()      # "the active account" default for tag arguments
PER_ACCOUNT_KV = ("player", "last_session", "no_progress", "fail_streak")
SEVERITIES = ("info", "warning", "error")


def test_key(tag, data_id):
    """kv key of an item's last dashboard Test, per account."""
    return f"test:{tag}:{data_id}" if tag else f"test:{data_id}"


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
    """`tag` pins the account (a session works on one account even if the
    dashboard switches meanwhile); otherwise it is the player_tag setting,
    read on every call. No tag at all (fresh install) = no filtering."""

    def __init__(self, path=DEFAULT_PATH, tag=None):
        self._tag = tag
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # timeout: the capture process and the dashboard write concurrently.
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._adopt_untagged()

    def close(self):
        self.db.close()

    def _migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(_MIGRATIONS[version:], start=version + 1):
            # One transaction per step: the schema and its version move together.
            self.db.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {i};\nCOMMIT;")

    # --- accounts -----------------------------------------------------------
    def active_tag(self):
        if self._tag:
            return self._tag
        r = self.db.execute("SELECT value FROM settings WHERE key='player_tag'").fetchone()
        return (json.loads(r[0]) or None) if r else None

    def pin(self):
        """Keep working on the account active now, even if it changes."""
        self._tag = self.active_tag()
        return self._tag

    def _t(self, tag):
        return self.active_tag() if tag is ACTIVE else tag

    def akey(self, name, tag=ACTIVE):
        """kv key of a per-account value."""
        tag = self._t(tag)
        return f"{name}:{tag}" if tag else name

    def _adopt_untagged(self):
        """Give rows from before accounts existed (tag NULL) to the account in
        use then: the player_tag setting, else the newest snapshot's tag."""
        tag = self.active_tag()
        if not tag:
            r = self.db.execute("SELECT tag FROM snapshots ORDER BY export_ts DESC LIMIT 1").fetchone()
            tag = r[0] if r else None
        if not tag:
            return
        untagged = sum(self.db.execute(f"SELECT COUNT(*) FROM {t} WHERE tag IS NULL").fetchone()[0]
                       for t in ("plan_items", "sessions", "actions", "farm_runs"))
        old_kv = self.db.execute("SELECT COUNT(*) FROM kv WHERE key IN (?,?,?,?) OR "
                                 "(key LIKE 'test:%' AND key NOT LIKE 'test:#%')",
                                 PER_ACCOUNT_KV).fetchone()[0]
        if not untagged and not old_kv and self.account(tag):
            return
        with self.db:
            for t in ("plan_items", "sessions", "actions", "farm_runs"):
                self.db.execute(f"UPDATE {t} SET tag=? WHERE tag IS NULL", (tag,))
            for k in PER_ACCOUNT_KV:
                self.db.execute("UPDATE OR IGNORE kv SET key=? WHERE key=?", (f"{k}:{tag}", k))
            for (k,) in self.db.execute("SELECT key FROM kv WHERE key LIKE 'test:%' "
                                        "AND key NOT LIKE 'test:#%'").fetchall():
                self.db.execute("UPDATE OR IGNORE kv SET key=? WHERE key=?",
                                (f"test:{tag}:{k[5:]}", k))
            name = self.db.execute("SELECT value FROM settings WHERE key='player_name'").fetchone()
            self.db.execute("INSERT OR IGNORE INTO accounts (tag, display_name, added_at) VALUES (?,?,?)",
                            (tag, (json.loads(name[0]) or None) if name else None, int(time.time())))
            self.db.execute("DELETE FROM settings WHERE key='player_name'")
            if not self.db.execute("SELECT 1 FROM settings WHERE key='player_tag' AND value!='\"\"'").fetchone():
                self.db.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES "
                                "('player_tag', ?, ?)", (json.dumps(tag), int(time.time())))

    _ACCOUNT_FIELDS = {"scid", "village", "display_name", "town_hall", "position", "switched_at"}

    def accounts(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM accounts ORDER BY COALESCE(position, 99), added_at, tag")]

    def account(self, tag):
        r = self.db.execute("SELECT * FROM accounts WHERE tag=?", (tag,)).fetchone()
        return dict(r) if r else None

    def upsert_account(self, tag, **fields):
        bad = set(fields) - self._ACCOUNT_FIELDS
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO accounts (tag, added_at) VALUES (?,?)",
                            (tag, int(time.time())))
            if fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE accounts SET {cols} WHERE tag=?", (*fields.values(), tag))
        return self.account(tag)

    def set_active(self, tag):
        """Make `tag` the account the dashboard and the automation work on."""
        self.upsert_account(tag, switched_at=int(time.time()))
        self.set_setting("player_tag", tag)

    def save_snapshot(self, snapshot, resources=None, game_version=None, captured_at=None,
                      counter=None, player=None):
        """Store a snapshot; returns its id. Saving the same export twice
        (same tag and timestamp) returns the existing id. `player` (name,
        clan, xp read from the home screen) is kept in kv "player"."""
        if player:
            self.kv_set(self.akey("player", snapshot.tag), dict(player, tag=snapshot.tag))
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

    def all(self, tag=ACTIVE, since=None):
        """All snapshots of the active account (or `tag`; None = every
        account), export time >= since, oldest first."""
        tag = self._t(tag)
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

    def latest(self, tag=ACTIVE, before_id=None):
        """Newest snapshot by export time of the active account (or `tag`;
        None = any), optionally older than a given snapshot id."""
        tag = self._t(tag)
        sql, args = "SELECT * FROM snapshots WHERE 1=1", []
        if tag is not None:
            sql += " AND tag=?"
            args.append(tag)
        if before_id is not None:
            sql += " AND export_ts < (SELECT export_ts FROM snapshots WHERE id=?)"
            args.append(before_id)
        sql += " ORDER BY export_ts DESC, id DESC LIMIT 1"
        return self._load(self.db.execute(sql, args).fetchone())

    def count(self, tag=ACTIVE):
        tag = self._t(tag)
        if tag is None:
            return self.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        return self.db.execute("SELECT COUNT(*) FROM snapshots WHERE tag=?", (tag,)).fetchone()[0]

    def resource_history(self, since, tag=ACTIVE):
        """[(export_ts, gold, elixir, dark_elixir, gems, builders_free, builders_total)]."""
        tag = self._t(tag)
        return [tuple(r) for r in self.db.execute(
            "SELECT export_ts, gold, elixir, dark_elixir, gems, builders_free, builders_total "
            "FROM snapshots WHERE export_ts >= ? AND (? IS NULL OR tag=?) ORDER BY export_ts",
            (since, tag, tag))]

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
        """The active account's upgrade queue, in order."""
        return [dict(r) | {"enabled": bool(r["enabled"])} for r in self.db.execute(
            "SELECT id, position, data_id, target_level, enabled, note, created_at "
            "FROM plan_items WHERE tag IS ? ORDER BY position, id", (self.active_tag(),))]

    def add_plan_item(self, data_id, target_level=None, note=""):
        tag = self.active_tag()
        with self.db:
            pos = self.db.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM plan_items "
                                  "WHERE tag IS ?", (tag,)).fetchone()[0]
            return self.db.execute(
                "INSERT INTO plan_items (position, data_id, target_level, note, created_at, tag) "
                "VALUES (?,?,?,?,?,?)",
                (pos, data_id, target_level, note, int(time.time()), tag)).lastrowid

    def update_plan_item(self, item_id, **fields):
        allowed = {"target_level", "enabled", "note"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot change {sorted(bad)}")
        if not fields:
            return False
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.db:
            cur = self.db.execute(f"UPDATE plan_items SET {cols} WHERE id=? AND tag IS ?",
                                  (*fields.values(), item_id, self.active_tag()))
        return cur.rowcount == 1

    def delete_plan_item(self, item_id):
        with self.db:
            return self.db.execute("DELETE FROM plan_items WHERE id=? AND tag IS ?",
                                   (item_id, self.active_tag())).rowcount == 1

    def reorder_plan(self, ids):
        """Set the queue order; `ids` must be exactly the current item ids."""
        current = {r[0] for r in self.db.execute("SELECT id FROM plan_items WHERE tag IS ?",
                                                 (self.active_tag(),))}
        if set(ids) != current or len(ids) != len(current):
            raise ValueError("order must list every queue item exactly once")
        with self.db:
            self.db.executemany("UPDATE plan_items SET position=? WHERE id=?",
                                [(pos, i) for pos, i in enumerate(ids)])

    # --- notifications ----------------------------------------------------
    def notify(self, severity, source, message, detail="", tag=ACTIVE):
        """Alerts are shown for every account, marked with the one they concern."""
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        with self.db:
            return self.db.execute(
                "INSERT INTO notifications (created_at, severity, source, message, detail, tag) "
                "VALUES (?,?,?,?,?,?)",
                (int(time.time()), severity, source, message, detail, self._t(tag))).lastrowid

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
                "INSERT INTO farm_runs (created_at, status, params, tag) VALUES (?, 'queued', ?, ?)",
                (int(time.time()), json.dumps(params), self.active_tag())).lastrowid

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

    def farm_runs(self, limit=20, tag=ACTIVE):
        """Newest first; the active account's (tag=None: every account's)."""
        tag = self._t(tag)
        return [self._farm_dict(r) for r in self.db.execute(
            "SELECT * FROM farm_runs WHERE (? IS NULL OR tag=?) ORDER BY id DESC LIMIT ?",
            (tag, tag, limit))]

    @staticmethod
    def _farm_dict(r):
        if r is None:
            return None
        d = dict(r)
        for k in ("params", "start_state", "state"):
            d[k] = json.loads(d[k]) if d[k] else None
        d["stop_requested"] = bool(d["stop_requested"])
        return d

    # --- orchestrator ---------------------------------------------------------
    def kv_get(self, key, default=None):
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def kv_set(self, key, value):
        with self.db:
            self.db.execute(
                "INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO "
                "UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), int(time.time())))

    def start_session(self, trigger):
        with self.db:
            return self.db.execute(
                "INSERT INTO sessions (trigger, started_at, status, pid, tag) VALUES (?,?,'running',?,?)",
                (trigger, int(time.time()), os.getpid(), self.active_tag())).lastrowid

    def end_session(self, session_id, status, summary):
        with self.db:
            self.db.execute("UPDATE sessions SET ended_at=?, status=?, summary=? WHERE id=?",
                            (int(time.time()), status, summary, session_id))

    def sessions(self, limit=30, tag=ACTIVE):
        """Newest first; the active account's (tag=None: every account's)."""
        tag = self._t(tag)
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM sessions WHERE (? IS NULL OR tag=?) ORDER BY id DESC LIMIT ?",
            (tag, tag, limit))]
        for r in rows:
            r["actions"] = self.actions(session_id=r["id"])
        return rows

    def add_action(self, session_id, kind, status, data_id=None, name="", from_level=None,
                   before_snapshot=None, detail=""):
        now = int(time.time())
        with self.db:
            return self.db.execute(
                "INSERT INTO actions (session_id, created_at, kind, data_id, name, from_level,"
                " before_snapshot, status, detail, updated_at, tag) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, now, kind, data_id, name, from_level, before_snapshot, status,
                 detail, now, self.active_tag())).lastrowid

    def update_action(self, action_id, status, detail=None):
        with self.db:
            if detail is None:
                self.db.execute("UPDATE actions SET status=?, updated_at=? WHERE id=?",
                                (status, int(time.time()), action_id))
            else:
                self.db.execute("UPDATE actions SET status=?, detail=?, updated_at=? WHERE id=?",
                                (status, detail, int(time.time()), action_id))

    def actions(self, session_id=None, status=None, limit=200, tag=ACTIVE):
        """Actions of the active account (tag=None: every account's)."""
        tag = self._t(tag)
        sql, args = "SELECT * FROM actions WHERE (? IS NULL OR tag=?)", [tag, tag]
        if session_id is not None:
            sql += " AND session_id=?"
            args.append(session_id)
        if status is not None:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY id LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(sql, args)]

    def add_command(self, kind):
        with self.db:
            return self.db.execute("INSERT INTO commands (created_at, kind) VALUES (?,?)",
                                   (int(time.time()), kind)).lastrowid

    def pending_commands(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM commands WHERE done_at IS NULL ORDER BY id")]

    def finish_command(self, command_id, result):
        with self.db:
            self.db.execute("UPDATE commands SET done_at=?, result=? WHERE id=?",
                            (int(time.time()), result, command_id))

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
