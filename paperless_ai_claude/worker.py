"""Queue, per-document processing, and the periodic loop."""
import json
import logging
import threading
import time
import traceback

from .claude import Claude, Refused, build_system, build_user, cut, load_prompt
from .resolver import resolve

log = logging.getLogger("paperless-ai-claude")


class Worker:
    def __init__(self, cfg, pl, db, claude=None):
        self.cfg, self.pl, self.db = cfg, pl, db
        self.claude = claude or Claude(cfg.anthropic_api_key, cfg.model)
        self.lock = threading.Lock()
        self.last_run = None
        self.stop = threading.Event()

    # --- metadata cache (refreshed per batch) -------------------------------
    def load_meta(self):
        return {"tags": self.pl.tags(), "correspondents": self.pl.correspondents(),
                "types": self.pl.document_types(), "custom_fields": self.pl.custom_fields()}

    def _tag_id(self, meta, name):
        for t in meta["tags"]:
            if t["name"].casefold() == name.casefold():
                return t["id"]
        return None

    def queue(self, meta, limit=None, extra=None):
        """Documents without the processed tag (and not failed), plus documents carrying the redo tag."""
        processed, redo, failed = (self._tag_id(meta, n) for n in (self.cfg.processed_tag, self.cfg.redo_tag, self.cfg.failed_tag))
        seen, out = set(), []
        if redo:
            for d in self.pl.documents({"tags__id__all": redo, "ordering": "-added"}):
                if d["id"] not in seen:
                    seen.add(d["id"]); out.append(d)
        params = {"ordering": "-added"}
        none = [str(i) for i in (processed, failed) if i]
        if none:
            params["tags__id__none"] = ",".join(none)
        if self.cfg.queue_since and not extra:          # the periodic loop only takes recent documents; backfill passes its own window
            params["added__date__gt"] = self.cfg.queue_since
        params.update(extra or {})
        for d in self.pl.documents(params):
            if d["id"] not in seen:
                seen.add(d["id"]); out.append(d)
            if limit and len(out) >= limit:
                break
        return out[:limit] if limit else out

    def queue_count(self, meta=None):
        meta = meta or self.load_meta()
        processed, failed = (self._tag_id(meta, n) for n in (self.cfg.processed_tag, self.cfg.failed_tag))
        params = {}
        none = [str(i) for i in (processed, failed) if i]
        if none:
            params["tags__id__none"] = ",".join(none)
        if self.cfg.queue_since:
            params["added__date__gt"] = self.cfg.queue_since
        return self.pl.count("/api/documents/", params)

    # --- one document ---------------------------------------------------------
    def process(self, doc_id, dry_run=False, meta=None):
        meta = meta or self.load_meta()
        doc = self.pl.document(doc_id)
        run_id = self.db.start(doc_id, doc.get("title"), dry_run, self.cfg.model)
        before = {k: doc.get(k) for k in ("title", "created_date", "tags", "correspondent", "document_type", "custom_fields")}
        try:
            content = (doc.get("content") or "").strip()
            if len(content) < 20:
                self._mark_failed(doc, meta, "no text", dry_run, force=True)   # retrying will not add text
                self.db.finish(run_id, "failed", before=before, error="no text (empty OCR)")
                return self.db.run(run_id)
            rules = load_prompt(self.cfg.prompt_path)
            system = build_system(rules, [t["name"] for t in meta["tags"] if t["name"] not in self.cfg.workflow_tags and t["name"] not in self.cfg.exclude_tags and not t.get("is_inbox_tag")],
                                  [c["name"] for c in meta["correspondents"]], [t["name"] for t in meta["types"]],
                                  [self.cfg.amount_field] if self.cfg.amount_field else [])
            result, usage = self.claude.classify(system, build_user(doc, cut(content, self.cfg.max_content_chars)))
            created = []

            def creator(kind, name, **extra):
                if dry_run:
                    obj = {"id": -(len(created) + 1), "name": name}
                else:
                    obj = self.pl.create(kind, name, **extra)
                    meta[{"tags": "tags", "correspondents": "correspondents", "document_types": "types", "custom_fields": "custom_fields"}[kind]].append(obj)
                created.append((kind, name))
                return obj

            patch, notes = resolve(self.cfg, doc, result, meta["tags"], meta["correspondents"], meta["types"], meta["custom_fields"], creator)
            after = dict(before)
            after.update({"created_date" if k == "created" else k: v for k, v in patch.items()})
            if dry_run:
                self.db.finish(run_id, "dry", before=before, after=after, output=result.model_dump(), usage=usage, notes=notes)
            else:
                if patch:
                    self.pl.update_document(doc_id, patch)
                self.db.finish(run_id, "ok", before=before, after=after, output=result.model_dump(), usage=usage, notes=notes)
            self._log("ok" if not dry_run else "dry", doc_id, title=after.get("title"), usage=usage, changed=sorted(patch))
        except Refused as e:
            self._mark_failed(doc, meta, str(e), dry_run, force=True)
            self.db.finish(run_id, "failed", before=before, error=str(e))
            self._log("refused", doc_id, error=str(e))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            prior = self.db.failures(doc_id)
            self.db.finish(run_id, "failed", before=before, error=err + "\n" + traceback.format_exc()[-1500:])
            self._mark_failed(doc, meta, err, dry_run, prior_failures=prior)
            self._log("failed", doc_id, error=err)
        return self.db.run(run_id)

    def _mark_failed(self, doc, meta, reason, dry_run, force=False, prior_failures=0):
        if dry_run:
            return
        if not force and prior_failures + 1 < self.cfg.max_failures:
            return
        failed = self._tag_id(meta, self.cfg.failed_tag)
        if failed is None:
            obj = self.pl.create("tags", self.cfg.failed_tag)
            meta["tags"].append(obj)
            failed = obj["id"]
        tags = set(doc.get("tags") or []) | {failed}
        try:
            self.pl.update_document(doc["id"], {"tags": sorted(tags)})
        except Exception as e:
            log.error("could not tag %s as failed: %s", doc["id"], e)

    # --- batches ------------------------------------------------------------------
    def run_batch(self, limit=None, dry_run=False, extra=None):
        if not self.lock.acquire(blocking=False):
            return {"skipped": "already running"}
        try:
            meta = self.load_meta()
            docs = self.queue(meta, limit or self.cfg.batch_size, extra)
            results = [self.process(d["id"], dry_run, meta) for d in docs]
            self.last_run = time.time()
            return {"processed": len(results), "ok": sum(1 for r in results if r["status"] in ("ok", "dry")),
                    "failed": sum(1 for r in results if r["status"] == "failed")}
        finally:
            self.lock.release()

    def loop(self):
        while not self.stop.is_set():
            try:
                res = self.run_batch()
                self._log("batch", 0, **res)
            except Exception as e:
                log.error("batch failed: %s", e)
            self.stop.wait(self.cfg.interval_min * 60)

    def _log(self, event, doc_id, **kw):
        if self.cfg.log_format == "json":
            log.info(json.dumps({"event": event, "doc": doc_id, **kw}, ensure_ascii=False, default=str))
        else:
            log.info("%s doc=%s %s", event, doc_id, " ".join(f"{k}={v}" for k, v in kw.items()))
