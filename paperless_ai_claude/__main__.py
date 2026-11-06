"""CLI: serve | run | backfill | backup | restore | status"""
import argparse
import json
import logging
import sys
import threading

from .backup import list_backups, restore, snapshot
from .config import Config
from .db import DB
from .paperless import Paperless
from .worker import Worker


def main(argv=None):
    ap = argparse.ArgumentParser(prog="paperless-ai-claude", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="web UI + periodic worker")
    r = sub.add_parser("run", help="process the queue once, or one document"); r.add_argument("--doc", type=int); r.add_argument("--limit", type=int); r.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("backfill", help="process older documents in batches"); b.add_argument("--before", help="added before YYYY-MM-DD (default: QUEUE_SINCE)"); b.add_argument("--limit", type=int, default=100); b.add_argument("--dry-run", action="store_true"); b.add_argument("--csv", help="write results to this CSV")
    sub.add_parser("backup", help="snapshot all document metadata")
    s = sub.add_parser("restore"); s.add_argument("--from", dest="src", required=True); s.add_argument("--doc", type=int)
    sub.add_parser("status")
    a = ap.parse_args(argv)
    cfg = Config(); cfg.check()
    logging.basicConfig(level=logging.INFO, format="%(message)s" if cfg.log_format == "json" else "%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    pl, db = Paperless(cfg.paperless_url, cfg.paperless_token), DB(cfg.db_path)
    if a.cmd == "backup":
        print(snapshot(pl, cfg.backup_dir, "manual")); return
    if a.cmd == "restore":
        restore(pl, a.src, a.doc); return
    w = Worker(cfg, pl, db)
    if a.cmd == "status":
        print(json.dumps({"queue": w.queue_count(), **db.stats()}, indent=1)); return
    if a.cmd == "run":
        if a.doc:
            run = w.process(a.doc, a.dry_run); print(json.dumps({k: run[k] for k in ("id", "status", "after", "notes", "usage", "error")}, ensure_ascii=False, indent=1, default=str))
        else:
            print(w.run_batch(a.limit, a.dry_run))
        return
    if a.cmd == "backfill":
        extra = {"added__date__lt": a.before or cfg.queue_since or "2999-12-31"}
        if not a.dry_run:
            print("backup:", snapshot(pl, cfg.backup_dir, "backfill"))
        res = w.run_batch(a.limit, a.dry_run, extra); print(res)
        if a.csv:
            import csv
            rows = db.recent(a.limit)
            with open(a.csv, "w", newline="", encoding="utf-8") as f:
                wr = csv.writer(f); wr.writerow(["run", "doc", "status", "title before", "title after", "correspondent", "type", "tags", "amount", "notes", "error"])
                for r in rows:
                    o = r.get("output") or {}
                    wr.writerow([r["id"], r["doc_id"], r["status"], (r.get("before") or {}).get("title"), (r.get("after") or {}).get("title"), o.get("correspondent"), o.get("document_type"), ",".join(o.get("tags") or []), o.get("amount"), "; ".join(r.get("notes") or []), (r.get("error") or "").split("\n")[0]])
            print("csv:", a.csv)
        return
    if a.cmd == "serve":
        import uvicorn
        from .web.app import create_app
        if cfg.interval_min > 0:
            threading.Thread(target=w.loop, daemon=True, name="worker").start()
        uvicorn.run(create_app(cfg, pl, db, w), host="0.0.0.0", port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
