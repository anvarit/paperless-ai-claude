"""Metadata snapshots and restore (document metadata only; files are never touched)."""
import json
import os
from datetime import datetime

DOC_FIELDS = "id,title,created,created_date,tags,correspondent,document_type,custom_fields"


def snapshot(pl, backup_dir, reason, doc_ids=None):
    d = os.path.join(backup_dir, datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + reason)
    os.makedirs(d, exist_ok=True)
    if doc_ids:
        docs = []
        ids = list(doc_ids)
        for i in range(0, len(ids), 100):
            docs += list(pl.all("/api/documents/", {"id__in": ",".join(map(str, ids[i:i + 100])), "fields": DOC_FIELDS}))
    else:
        docs = list(pl.all("/api/documents/", {"fields": DOC_FIELDS}))
    for name, data in (("documents.json", docs), ("tags.json", pl.tags()), ("correspondents.json", pl.correspondents()),
                       ("document_types.json", pl.document_types()), ("custom_fields.json", pl.custom_fields())):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return d


def list_backups(backup_dir):
    if not os.path.isdir(backup_dir):
        return []
    out = []
    for n in sorted(os.listdir(backup_dir), reverse=True):
        p = os.path.join(backup_dir, n, "documents.json")
        if os.path.exists(p):
            out.append({"name": n, "documents": sum(1 for _ in json.load(open(p, encoding="utf-8"))), "path": os.path.join(backup_dir, n)})
    return out


def restore(pl, backup_path, doc_id=None, log=print):
    """Put title/created/tags/correspondent/document_type/custom_fields back. Ids that no longer exist are skipped."""
    docs = json.load(open(os.path.join(backup_path, "documents.json"), encoding="utf-8"))
    tag_ids = {t["id"] for t in pl.tags()}
    corr_ids = {c["id"] for c in pl.correspondents()}
    type_ids = {t["id"] for t in pl.document_types()}
    cf_ids = {f["id"] for f in pl.custom_fields()}
    n = 0
    for d in docs:
        if doc_id and d["id"] != doc_id:
            continue
        patch = {"title": d["title"], "tags": [t for t in d["tags"] if t in tag_ids],
                 "correspondent": d["correspondent"] if d["correspondent"] in corr_ids else None,
                 "document_type": d["document_type"] if d["document_type"] in type_ids else None,
                 "custom_fields": [f for f in d.get("custom_fields") or [] if f["field"] in cf_ids]}
        created = d.get("created_date") or (d.get("created") or "")[:10]
        if created:
            patch["created"] = created
        try:
            pl.update_document(d["id"], patch)
            n += 1
        except Exception as e:  # keep going; report at the end
            log(f"restore {d['id']}: {e}")
    log(f"restored {n} documents from {backup_path}")
    return n
