"""Turn a Classification into a Paperless PATCH, matching names against existing objects."""
import re
from datetime import date


def normalise(name):
    n = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()
    return n or (name or "").casefold().strip()


class Index:
    """name -> object lookup, case/punctuation-insensitive."""
    def __init__(self, objects):
        self.by_norm = {}
        self.by_id = {}
        for o in objects:
            self.by_norm.setdefault(normalise(o["name"]), o)
            self.by_id[o["id"]] = o

    def find(self, name):
        return self.by_norm.get(normalise(name)) if name else None

    def add(self, obj):
        self.by_norm[normalise(obj["name"])] = obj
        self.by_id[obj["id"]] = obj


def resolve(cfg, doc, result, tags, correspondents, types, custom_fields, creator):
    """Return (patch, notes). creator(kind, name) creates an object and returns it; only called when allowed."""
    patch, notes = {}, []
    unreadable = getattr(result, "readable", True) is False
    if unreadable:
        notes.append("unreadable scan: metadata left unchanged")
    # --- tags
    tag_idx = Index(tags)
    excluded = set(cfg.exclude_tags) | cfg.workflow_tags | {t["name"] for t in tags if t.get("is_inbox_tag")}
    keep = set(doc.get("tags") or [])
    new_ids = []
    for name in ([] if unreadable else (result.tags or []))[: cfg.max_tags]:
        name = name.strip()
        if not name:
            continue
        if cfg.tag_case == "lower":
            name = name.lower()
        hit = tag_idx.find(name)
        if hit and hit["name"] in excluded:
            notes.append(f"tag '{name}' is reserved, skipped")
            continue
        if hit is None:
            if cfg.allow_new_tags:
                hit = creator("tags", name)
                tag_idx.add(hit)
                notes.append(f"created tag '{name}'")
            else:
                notes.append(f"unmatched tag '{name}' dropped")
                continue
        new_ids.append(hit["id"])
    processed = tag_idx.find(cfg.processed_tag) or creator("tags", cfg.processed_tag)
    redo = tag_idx.find(cfg.redo_tag)
    failed = tag_idx.find(cfg.failed_tag)
    final = (keep | set(new_ids) | {processed["id"]}) - {t["id"] for t in (redo, failed) if t}
    if final != keep:
        patch["tags"] = sorted(final)
    if unreadable:
        return patch, notes
    # --- correspondent
    if result.correspondent and (doc.get("correspondent") is None or cfg.overwrite_correspondent):
        idx = Index(correspondents)
        hit = idx.find(result.correspondent)
        if hit is None and cfg.allow_new_correspondents:
            hit = creator("correspondents", result.correspondent.strip())
            notes.append(f"created correspondent '{result.correspondent}'")
        if hit is None:
            notes.append(f"unmatched correspondent '{result.correspondent}' dropped")
        elif hit["id"] != doc.get("correspondent"):
            patch["correspondent"] = hit["id"]
    # --- document type
    if result.document_type and (doc.get("document_type") is None or cfg.overwrite_document_type):
        idx = Index(types)
        name = result.document_type.lower() if cfg.tag_case == "lower" else result.document_type
        hit = idx.find(name)
        if hit is None and cfg.allow_new_document_types:
            hit = creator("document_types", name.strip())
            notes.append(f"created document type '{name}'")
        if hit is None:
            notes.append(f"unmatched document type '{result.document_type}' dropped")
        elif hit["id"] != doc.get("document_type"):
            patch["document_type"] = hit["id"]
    # --- title
    title = (result.title or "").strip()
    if title and cfg.overwrite_title and title != doc.get("title"):
        patch["title"] = title[:128]
    # --- date: only when Paperless had nothing better than the added date, unless overwrite
    d = _valid_date(result.document_date)
    created = (doc.get("created_date") or (doc.get("created") or "")[:10]) or ""
    added = (doc.get("added") or "")[:10]
    if d and d != created and (cfg.overwrite_date or created == added or not created):
        patch["created"] = d
    # --- amount custom field
    if cfg.amount_field and result.amount is not None:
        cf = next((f for f in custom_fields if f["name"].casefold() == cfg.amount_field.casefold()), None)
        if cf is None:
            cf = creator("custom_fields", cfg.amount_field, data_type="float")
            notes.append(f"created custom field '{cfg.amount_field}'")
        fields = [f for f in (doc.get("custom_fields") or []) if f["field"] != cf["id"]]
        fields.append({"field": cf["id"], "value": round(float(result.amount), 2)})
        if fields != (doc.get("custom_fields") or []):
            patch["custom_fields"] = fields
    return patch, notes


def _valid_date(s):
    if not s or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        y, m, d = map(int, s.split("-"))
        dt = date(y, m, d)
    except ValueError:
        return None
    if dt.year < 1950 or dt > date.today().replace(year=date.today().year + 1):
        return None
    return s
