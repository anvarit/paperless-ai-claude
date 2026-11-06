"""Run history in SQLite (standard library)."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  started TEXT NOT NULL,
  finished TEXT,
  status TEXT NOT NULL,          -- ok | dry | failed | skipped
  dry_run INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  title TEXT,
  before TEXT, after TEXT, output TEXT, usage TEXT, notes TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS runs_doc ON runs(doc_id);
"""


class DB:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def start(self, doc_id, title, dry_run, model):
        with self.lock, self._conn() as c:
            cur = c.execute("INSERT INTO runs(doc_id, started, status, dry_run, model, title) VALUES (?,?,?,?,?,?)",
                            (doc_id, self.now(), "running", int(dry_run), model, title))
            return cur.lastrowid

    def finish(self, run_id, status, **fields):
        cols = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}
        cols["status"], cols["finished"] = status, self.now()
        with self.lock, self._conn() as c:
            c.execute(f"UPDATE runs SET {', '.join(k + '=?' for k in cols)} WHERE id=?", (*cols.values(), run_id))

    def run(self, run_id):
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return self._row(r) if r else None

    def recent(self, limit=50, doc_id=None):
        with self._conn() as c:
            q, args = "SELECT * FROM runs", ()
            if doc_id:
                q, args = q + " WHERE doc_id=?", (doc_id,)
            return [self._row(r) for r in c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit))]

    def failures(self, doc_id):
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM runs WHERE doc_id=? AND status='failed' AND dry_run=0", (doc_id,)).fetchone()[0]

    def stats(self):
        with self._conn() as c:
            today = datetime.now(timezone.utc).date().isoformat()
            rows = c.execute("SELECT status, dry_run, usage FROM runs WHERE started >= ?", (today,)).fetchall()
            month = c.execute("SELECT usage FROM runs WHERE started >= ? AND status='ok'", (today[:7],)).fetchall()
            def tokens(rs):
                t = {"input": 0, "output": 0, "cache_read": 0}
                for r in rs:
                    u = json.loads(r["usage"]) if r["usage"] else {}
                    for k in t:
                        t[k] += u.get(k, 0)
                return t
            return {"today_ok": sum(1 for r in rows if r["status"] == "ok"), "today_failed": sum(1 for r in rows if r["status"] == "failed"),
                    "today_dry": sum(1 for r in rows if r["status"] == "dry"), "today_tokens": tokens(rows), "month_tokens": tokens(month)}

    @staticmethod
    def _row(r):
        d = dict(r)
        for k in ("before", "after", "output", "usage", "notes"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d
