import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from paperless_ai_claude.config import Config
from paperless_ai_claude.db import DB
from paperless_ai_claude.worker import Worker
from paperless_ai_claude.claude import Refused
from fakes import FakePaperless, FakeClaude

TAGS = [{"id": 1, "name": "ai-processed"}, {"id": 2, "name": "insurance"}, {"id": 3, "name": "business"}, {"id": 4, "name": "Inbox", "is_inbox_tag": True}, {"id": 5, "name": "ai-redo"}]
CORR = [{"id": 10, "name": "Proximus"}]
TYPES = [{"id": 20, "name": "invoice"}, {"id": 21, "name": "letter"}]
CF = [{"id": 1, "name": "Amount", "data_type": "float"}]
DOC = {"id": 100, "title": "scan_0001.pdf", "content": "PROXIMUS NV factuur 2026-0098123 datum 12/10/2026 totaal 79,00 EUR " * 3,
       "created_date": "2026-11-01", "added": "2026-11-01T10:00:00+01:00", "tags": [4], "correspondent": None, "document_type": None, "custom_fields": []}
RESULT = {"title": "Invoice: Proximus 12-10-2026 (2026-0098123)", "correspondent": "proximus", "document_type": "Invoice",
          "tags": ["insurance", "telecom", "Business"], "document_date": "2026-10-12", "language": "nl", "amount": 79.0}


def make(tmp, docs=None, result=RESULT, error=None, **env):
    os.environ.update({"PAPERLESS_URL": "http://x", "PAPERLESS_TOKEN": "t", "ANTHROPIC_API_KEY": "k", "DATA_DIR": tmp, "AMOUNT_FIELD": "Amount", **env})
    cfg = Config()
    pl = FakePaperless(docs or [DOC], TAGS, CORR, TYPES, CF)
    db = DB(cfg.db_path)
    return cfg, pl, db, Worker(cfg, pl, db, FakeClaude(result, error))


def test_live_run_patches_document():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp)
        run = w.process(100)
        assert run["status"] == "ok", run
        (doc_id, patch), = pl.patches
        assert patch["title"].startswith("Invoice: Proximus")
        assert patch["correspondent"] == 10 and patch["document_type"] == 20
        assert set(patch["tags"]) == {4, 1, 2, 3}            # inbox kept, processed added, insurance + business matched
        assert patch["created"] == "2026-10-12"             # created equalled added -> date taken from the document
        assert patch["custom_fields"] == [{"field": 1, "value": 79.0}]
        assert "unmatched tag 'telecom' dropped" in run["notes"]
        assert db.stats()["today_ok"] == 1


def test_dry_run_changes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp)
        run = w.process(100, dry_run=True)
        assert run["status"] == "dry" and pl.patches == [] and run["after"]["title"].startswith("Invoice")


def test_queue_and_batch():
    with tempfile.TemporaryDirectory() as tmp:
        done = dict(DOC, id=101, tags=[1], added="2026-11-02T10:00:00+01:00")
        redo = dict(DOC, id=102, tags=[1, 5], added="2026-10-01T10:00:00+01:00")
        cfg, pl, db, w = make(tmp, docs=[DOC, done, redo])
        ids = [d["id"] for d in w.queue(w.load_meta())]
        assert ids == [102, 100]                            # redo first, processed skipped
        res = w.run_batch()
        assert res == {"processed": 2, "ok": 2, "failed": 0}
        assert 5 not in pl.docs[102]["tags"] and 1 in pl.docs[102]["tags"]
        assert w.queue_count() == 0


def test_empty_text_fails_and_tags_after_max_failures():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp, docs=[dict(DOC, content="")], MAX_FAILURES="2")
        assert w.process(100)["status"] == "failed"                       # empty text fails and is tagged at once
        assert pl.patches and any(t["name"] == "ai-failed" for t in pl.tags()) and 1001 in pl.patches[-1][1]["tags"]
    with tempfile.TemporaryDirectory() as tmp:
        from paperless_ai_claude.paperless import PaperlessError
        cfg, pl, db, w = make(tmp, error=PaperlessError("boom"), MAX_FAILURES="2")
        assert w.process(100)["status"] == "failed" and pl.patches == []  # first transient failure: no tag yet
        assert w.process(100)["status"] == "failed" and pl.patches         # second: tagged


def test_refusal_marks_failed_immediately():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp, error=Refused("refused (cyber)"))
        run = w.process(100)
        assert run["status"] == "failed" and "refused" in run["error"] and pl.patches


def test_existing_correspondent_kept_unless_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp, docs=[dict(DOC, correspondent=99)])
        w.process(100)
        assert "correspondent" not in pl.patches[0][1]
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp, docs=[dict(DOC, correspondent=99)], OVERWRITE_CORRESPONDENT="true")
        w.process(100)
        assert pl.patches[0][1]["correspondent"] == 10


def test_new_tag_created_only_when_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp, ALLOW_NEW_TAGS="true")
        run = w.process(100)
        assert any(t["name"] == "telecom" for t in pl.tags()) and "created tag 'telecom'" in run["notes"]


def test_backup_and_restore():
    from paperless_ai_claude.backup import snapshot, restore, list_backups
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp)
        d = snapshot(pl, cfg.backup_dir, "test")
        w.process(100)
        assert pl.docs[100]["title"].startswith("Invoice")
        assert restore(pl, d, log=lambda m: None) == 1
        assert pl.docs[100]["title"] == "scan_0001.pdf" and pl.docs[100]["tags"] == [4]
        assert list_backups(cfg.backup_dir)[0]["documents"] == 1


def test_web_pages_render():
    from fastapi.testclient import TestClient
    from paperless_ai_claude.web.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        cfg, pl, db, w = make(tmp)
        c = TestClient(create_app(cfg, pl, db, w))
        assert c.get("/").status_code == 200 and "Queue" in c.get("/").text
        r = c.post("/documents/100/process", data={"dry_run": "1"}, follow_redirects=True)
        assert r.status_code == 200 and "Invoice: Proximus" in r.text and "dry run" in r.text
        assert c.get("/prompt").status_code == 200 and c.get("/backups").status_code == 200 and c.get("/settings").status_code == 200
        assert "***" in c.get("/settings").text and c.get("/api/status").json()["queue"] == 1
        r = c.post("/prompt", data={"text": "custom rules"}, follow_redirects=True)
        assert "custom rules" in r.text and os.path.exists(cfg.prompt_path)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
