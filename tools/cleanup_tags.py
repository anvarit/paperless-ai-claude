#!/usr/bin/env python3
"""Tag hygiene for Paperless-ngx. Standard library only.

  cleanup_tags.py report --out DIR            write DIR/tags.csv and DIR/plan.json, change nothing
  cleanup_tags.py apply  --plan DIR/plan.json  back up, then merge/rename/delete as planned

Environment: PAPERLESS_URL, PAPERLESS_TOKEN, BACKUP_DIR (default ./backups),
EXCLUDE_TAGS (comma-separated names never touched), TAG_CASE (lower|keep, default lower).

Classes in the report:
  amount   name is only a money amount ("114.19", "amount: 10.00")  -> delete
  empty    0 documents                                              -> delete
  variant  same name as another tag after normalisation             -> merge into the biggest
  keep     everything else (rename to lowercase when TAG_CASE=lower)
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime

AMOUNT = re.compile(r"^(amount|bedrag|montant|total|totaal)?[:=\s]*[€$£]?\s*\d+([.,]\d+)*\s*(eur|euro|€|usd|\$)?$", re.I)


def normalise(name: str) -> str:
    n = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    return n or name.casefold().strip()   # non-Latin names keep their own identity


def classify(tags, exclude):
    """tags: list of dicts with id, name, document_count. Returns list of (tag, cls, merge_into_id)."""
    groups = defaultdict(list)
    for t in tags:
        groups[normalise(t["name"])].append(t)
    out = []
    for t in tags:
        if t["name"] in exclude:
            out.append((t, "keep", None))
        elif AMOUNT.match(t["name"].strip()):
            out.append((t, "amount", None))
        elif t["document_count"] == 0:
            out.append((t, "empty", None))
        else:
            grp = [g for g in groups[normalise(t["name"])] if g["document_count"] > 0 and not AMOUNT.match(g["name"].strip())]
            target = max(grp, key=lambda g: (g["document_count"], g["name"] == g["name"].lower(), -g["id"]))
            out.append((t, "variant", target["id"]) if target["id"] != t["id"] else (t, "keep", None))
    return out


class Paperless:
    def __init__(self, url, token):
        self.url = url.rstrip("/")
        self.token = token

    def req(self, method, path, data=None, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(self.url + path + q, data=body, method=method,
                                   headers={"Authorization": f"Token {self.token}", "Content-Type": "application/json",
                                            "Accept": "application/json; version=9"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path}{q} -> {e.code} {e.read()[:300]!r}")

    def all(self, path, params=None):
        params = dict(params or {}, page_size=100, page=1)
        while True:
            page = self.req("GET", path, params=params)
            yield from page["results"]
            if not page.get("next"):
                return
            params["page"] += 1

    def tags(self):
        return list(self.all("/api/tags/", {"fields": "id,name,document_count", "ordering": "name"}))

    def doc_ids_with_tag(self, tag_id):
        return [d["id"] for d in self.all("/api/documents/", {"tags__id__all": tag_id, "fields": "id"})]

    def docs_with_tags(self, tag_ids):
        if not tag_ids:
            return []
        return list(self.all("/api/documents/", {"tags__id__in": ",".join(map(str, tag_ids)), "fields": "id,tags"}))

    def modify_tags(self, doc_ids, add, remove):
        for i in range(0, len(doc_ids), 500):
            self.req("POST", "/api/documents/bulk_edit/", {"documents": doc_ids[i:i + 500], "method": "modify_tags",
                                                            "parameters": {"add_tags": add, "remove_tags": remove}})

    def get_or_create_tag(self, name):
        for t in self.all("/api/tags/", {"name__iexact": name, "fields": "id,name"}):
            if t["name"].casefold() == name.casefold():
                return t
        return self.req("POST", "/api/tags/", {"name": name, "matching_algorithm": 0})

    def rename_tag(self, tag_id, name):
        self.req("PATCH", f"/api/tags/{tag_id}/", {"name": name})

    def delete_tag(self, tag_id):
        self.req("DELETE", f"/api/tags/{tag_id}/")


def report(pl, out, exclude, tag_case):
    os.makedirs(out, exist_ok=True)
    tags = pl.tags()
    rows = classify(tags, exclude)
    by_id = {t["id"]: t for t in tags}
    plan = {"created": datetime.now().isoformat(timespec="seconds"), "delete": [], "merge": [], "rename": []}
    with open(os.path.join(out, "tags.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "documents", "class", "merge_into"])
        for t, cls, target in rows:
            w.writerow([t["id"], t["name"], t["document_count"], cls, by_id[target]["name"] if target else ""])
            if cls in ("amount", "empty"):
                plan["delete"].append({"id": t["id"], "name": t["name"], "documents": t["document_count"], "why": cls})
            elif cls == "variant":
                plan["merge"].append({"from": t["id"], "from_name": t["name"], "into": target, "into_name": by_id[target]["name"]})
            elif tag_case == "lower" and t["name"] != t["name"].lower():
                plan["rename"].append({"id": t["id"], "from": t["name"], "to": t["name"].lower()})
    with open(os.path.join(out, "plan.json"), "w") as f:
        json.dump(plan, f, indent=1, ensure_ascii=False)
    n = lambda c: sum(1 for _, cls, _ in rows if cls == c)
    print(f"tags: {len(tags)}  amount: {n('amount')}  empty: {n('empty')}  variant: {n('variant')}  keep: {n('keep')}"
          f"  renames: {len(plan['rename'])}  -> {out}/tags.csv, plan.json")


def backup(pl, backup_dir, tag_ids, reason):
    d = os.path.join(backup_dir, datetime.now().strftime("%Y%m%d-%H%M") + "-" + reason)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "tags.json"), "w") as f:
        json.dump(pl.tags(), f, ensure_ascii=False)
    with open(os.path.join(d, "documents.json"), "w") as f:
        json.dump(pl.docs_with_tags(tag_ids), f)
    print(f"backup written to {d}")
    return d


def apply(pl, plan_path, backup_dir, exclude):
    plan = json.load(open(plan_path))
    touched = [x["id"] for x in plan["delete"]] + [x["from"] for x in plan["merge"]] + [x["into"] for x in plan["merge"] if x.get("into")]
    backup(pl, backup_dir, touched, "tag-cleanup")
    created = {}
    for m in plan["merge"]:
        if m["from_name"] in exclude:
            continue
        if m.get("into") is None:
            if m["into_name"] not in created:
                created[m["into_name"]] = pl.get_or_create_tag(m["into_name"])["id"]
            m["into"] = created[m["into_name"]]
        ids = pl.doc_ids_with_tag(m["from"])
        if ids:
            pl.modify_tags(ids, add=[m["into"]], remove=[m["from"]])
        pl.delete_tag(m["from"])
        print(f"merged {m['from_name']!r} -> {m['into_name']!r} ({len(ids)} docs)")
    for r in plan["rename"]:
        if r["from"] in exclude:
            continue
        pl.rename_tag(r["id"], r["to"])
        print(f"renamed {r['from']!r} -> {r['to']!r}")
    for x in plan["delete"]:
        if x["name"] in exclude:
            continue
        pl.delete_tag(x["id"])
        print(f"deleted {x['name']!r} ({x['why']}, {x['documents']} docs)")
    print(f"done: {len(plan['merge'])} merges, {len(plan['rename'])} renames, {len(plan['delete'])} deletes")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("--out", required=True)
    a = sub.add_parser("apply"); a.add_argument("--plan", required=True)
    args = ap.parse_args()
    url, token = os.environ.get("PAPERLESS_URL"), os.environ.get("PAPERLESS_TOKEN")
    if not url or not token:
        sys.exit("set PAPERLESS_URL and PAPERLESS_TOKEN")
    exclude = {s.strip() for s in os.environ.get("EXCLUDE_TAGS", "ai-processed,ai-redo,ai-failed").split(",") if s.strip()}
    pl = Paperless(url, token)
    if args.cmd == "report":
        report(pl, args.out, exclude, os.environ.get("TAG_CASE", "lower"))
    else:
        apply(pl, args.plan, os.environ.get("BACKUP_DIR", "./backups"), exclude)


if __name__ == "__main__":
    main()
