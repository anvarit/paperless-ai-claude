"""Small web UI + JSON status. Plain HTML forms, no build step."""
import os
import threading

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..backup import list_backups, restore, snapshot
from ..claude import DEFAULT_PROMPT, load_prompt

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def create_app(cfg, pl, db, worker):
    app = FastAPI(title="paperless-ai-claude")
    app.state.messages = []

    def flash(msg):
        app.state.messages.append(msg)

    def ctx(request, **kw):
        msgs, app.state.messages = app.state.messages, []
        return {"request": request, "cfg": cfg, "version": __version__, "messages": msgs, **kw}

    def background(fn, *a, **kw):
        threading.Thread(target=fn, args=a, kwargs=kw, daemon=True).start()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        try:
            meta = worker.load_meta()
            queue, failed = worker.queue_count(meta), worker.failed_count(meta)
        except Exception as e:
            queue, failed = f"error: {e}", "?"
        return templates.TemplateResponse(request, "index.html", ctx(request, runs=db.recent(50), stats=db.stats(), queue=queue, failed=failed,
                                                                     running=worker.lock.locked(), last_run=worker.last_run))

    @app.post("/run")
    def run_now(limit: int = Form(0), dry_run: int = Form(0)):
        background(worker.run_batch, limit or None, bool(dry_run))
        flash(f"batch started ({'dry run' if dry_run else 'live'})")
        return RedirectResponse("/", status_code=303)

    @app.post("/documents/{doc_id}/process")
    def process_doc(doc_id: int, dry_run: int = Form(0)):
        run = worker.process(doc_id, bool(dry_run))
        return RedirectResponse(f"/runs/{run['id']}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: int):
        run = db.run(run_id)
        if not run:
            return PlainTextResponse("no such run", status_code=404)
        names = {}
        try:
            meta = worker.load_meta()
            names = {"tags": {t["id"]: t["name"] for t in meta["tags"]}, "correspondents": {c["id"]: c["name"] for c in meta["correspondents"]},
                     "types": {t["id"]: t["name"] for t in meta["types"]}, "custom_fields": {f["id"]: f["name"] for f in meta["custom_fields"]}}
        except Exception:
            pass
        return templates.TemplateResponse(request, "run.html", ctx(request, run=run, names=names, history=db.recent(20, run["doc_id"])))

    @app.get("/prompt", response_class=HTMLResponse)
    def prompt_page(request: Request):
        return templates.TemplateResponse(request, "prompt.html", ctx(request, prompt=load_prompt(cfg.prompt_path), custom=os.path.exists(cfg.prompt_path)))

    @app.post("/prompt")
    def prompt_save(text: str = Form(...), reset: int = Form(0)):
        if reset:
            if os.path.exists(cfg.prompt_path):
                os.remove(cfg.prompt_path)
            flash("prompt reset to the shipped default")
        else:
            os.makedirs(os.path.dirname(cfg.prompt_path), exist_ok=True)
            with open(cfg.prompt_path, "w", encoding="utf-8") as f:
                f.write(text.replace("\r\n", "\n"))
            flash("prompt saved; used from the next run")
        return RedirectResponse("/prompt", status_code=303)

    @app.get("/backups", response_class=HTMLResponse)
    def backups_page(request: Request):
        return templates.TemplateResponse(request, "backups.html", ctx(request, backups=list_backups(cfg.backup_dir)))

    @app.post("/backups")
    def backups_create():
        d = snapshot(pl, cfg.backup_dir, "manual")
        flash(f"snapshot written: {d}")
        return RedirectResponse("/backups", status_code=303)

    @app.post("/backups/restore")
    def backups_restore(name: str = Form(...), doc_id: int = Form(0)):
        path = os.path.join(cfg.backup_dir, os.path.basename(name))
        if not os.path.exists(os.path.join(path, "documents.json")):
            flash("no such backup")
        else:
            n = restore(pl, path, doc_id or None, log=lambda m: None)
            flash(f"restored {n} document(s) from {name}")
        return RedirectResponse("/backups", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return templates.TemplateResponse(request, "settings.html", ctx(request, settings=cfg.public(), default_prompt=DEFAULT_PROMPT))

    @app.get("/api/status")
    def status():
        try:
            meta = worker.load_meta()
            queue, failed = worker.queue_count(meta), worker.failed_count(meta)
        except Exception:
            queue = failed = None
        s = db.stats()
        return JSONResponse({"version": __version__, "queue": queue, "failed": failed, "running": worker.lock.locked(), "last_run": worker.last_run, **s})

    return app
