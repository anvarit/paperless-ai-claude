#!/usr/bin/env python3
"""Document-type hygiene for Paperless-ngx: map existing document types onto a vocabulary.

  cleanup_doctypes.py plan  --vocab vocab.csv --out DIR [--delete-below 3]   write DIR/doctypes.csv, plan.json, review.csv
  cleanup_doctypes.py apply --plan DIR/plan.json                              back up, then merge/rename/delete

vocab.csv has the same shape as for vocabulary.py (target,pattern,...; "-" deletes). Merging a type
means: every document of the old type gets the target type (bulk edit), then the old type is deleted.
Types with zero documents are deleted. Unmatched types with fewer than --delete-below documents are
deleted (their documents keep no type; the classifier sets one later), others go to review.csv.
Environment: PAPERLESS_URL, PAPERLESS_TOKEN, BACKUP_DIR (default ./backups).
"""
import argparse, csv, json, os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from cleanup_tags import Paperless
from vocabulary import load_vocab


def plan(types, vocab, delete_below):
    merges, deletes, renames, review = [], [], [], []
    existing = {t["name"].casefold(): t for t in types}
    members = {}
    for t in types:
        if t["document_count"] == 0:
            deletes.append({"id": t["id"], "name": t["name"], "documents": 0, "why": "empty"}); continue
        hit = next((target for target, pats in vocab if any(p.fullmatch(t["name"]) for p in pats)), None)
        if hit is None:
            (deletes if t["document_count"] < delete_below else review).append(
                {"id": t["id"], "name": t["name"], "documents": t["document_count"], "why": "not in vocabulary"}); continue
        if hit == "-":
            deletes.append({"id": t["id"], "name": t["name"], "documents": t["document_count"], "why": "explicit"}); continue
        members.setdefault(hit, []).append(t)
    for target, ms in members.items():
        tgt = existing.get(target.casefold())
        if tgt and tgt["name"] != target:
            renames.append({"id": tgt["id"], "from": tgt["name"], "to": target})
        for t in ms:
            if tgt and t["id"] == tgt["id"]:
                continue
            merges.append({"from": t["id"], "from_name": t["name"], "documents": t["document_count"], "into": tgt["id"] if tgt else None, "into_name": target})
    return merges, deletes, renames, review, members


def cmd_plan(pl, a):
    types = list(pl.all("/api/document_types/", {"fields": "id,name,document_count", "ordering": "name"}))
    merges, deletes, renames, review, members = plan(types, load_vocab(a.vocab), a.delete_below)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "doctypes.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "name", "documents"]); [w.writerow([t["id"], t["name"], t["document_count"]]) for t in types]
    json.dump({"merge": merges, "delete": deletes, "rename": renames}, open(os.path.join(a.out, "plan.json"), "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(a.out, "review.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "name", "documents"]); [w.writerow([r["id"], r["name"], r["documents"]]) for r in sorted(review, key=lambda r: -r["documents"])]
    print(f"types: {len(types)}  targets: {len(members)}  merges: {len(merges)}  renames: {len(renames)}  deletes: {len(deletes)}  review: {len(review)}")
    for target, ms in sorted(members.items(), key=lambda kv: -sum(m["document_count"] for m in kv[1])):
        print(f'  {target:22} ~{sum(m["document_count"] for m in ms):>5}  <- ' + "; ".join(f'{m["name"]}:{m["document_count"]}' for m in sorted(ms, key=lambda m: -m["document_count"])[:12]))


def docs_of_type(pl, type_id):
    return [d["id"] for d in pl.all("/api/documents/", {"document_type__id": type_id, "fields": "id"})]


def cmd_apply(pl, a):
    p = json.load(open(a.plan))
    d = os.path.join(os.environ.get("BACKUP_DIR", "./backups"), datetime.now().strftime("%Y%m%d-%H%M") + "-doctype-cleanup")
    os.makedirs(d, exist_ok=True)
    json.dump(list(pl.all("/api/document_types/", {"fields": "id,name,document_count"})), open(os.path.join(d, "document_types.json"), "w"), ensure_ascii=False)
    touched = [x["id"] for x in p["delete"]] + [x["from"] for x in p["merge"]]
    json.dump(list(pl.all("/api/documents/", {"document_type__id__in": ",".join(map(str, touched)), "fields": "id,document_type"})) if touched else [],
              open(os.path.join(d, "documents.json"), "w"))
    print("backup written to", d)
    created = {}
    for m in p["merge"]:
        if m["into"] is None:
            if m["into_name"] not in created:
                hit = [t for t in pl.all("/api/document_types/", {"name__iexact": m["into_name"], "fields": "id,name"}) if t["name"].casefold() == m["into_name"].casefold()]
                created[m["into_name"]] = hit[0]["id"] if hit else pl.req("POST", "/api/document_types/", {"name": m["into_name"], "matching_algorithm": 0})["id"]
            m["into"] = created[m["into_name"]]
        ids = docs_of_type(pl, m["from"])
        for i in range(0, len(ids), 500):
            pl.req("POST", "/api/documents/bulk_edit/", {"documents": ids[i:i + 500], "method": "set_document_type", "parameters": {"document_type": m["into"]}})
        pl.req("DELETE", f"/api/document_types/{m['from']}/")
        print(f"merged {m['from_name']!r} -> {m['into_name']!r} ({len(ids)} docs)")
    for r in p["rename"]:
        pl.req("PATCH", f"/api/document_types/{r['id']}/", {"name": r["to"]}); print(f"renamed {r['from']!r} -> {r['to']!r}")
    for x in p["delete"]:
        pl.req("DELETE", f"/api/document_types/{x['id']}/"); print(f"deleted {x['name']!r} ({x['why']}, {x['documents']} docs)")
    print(f"done: {len(p['merge'])} merges, {len(p['rename'])} renames, {len(p['delete'])} deletes")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("--vocab", required=True); s.add_argument("--out", required=True); s.add_argument("--delete-below", type=int, default=3)
    s = sub.add_parser("apply"); s.add_argument("--plan", required=True)
    a = ap.parse_args()
    url, token = os.environ.get("PAPERLESS_URL"), os.environ.get("PAPERLESS_TOKEN")
    if not url or not token:
        sys.exit("set PAPERLESS_URL and PAPERLESS_TOKEN")
    pl = Paperless(url, token)
    (cmd_plan if a.cmd == "plan" else cmd_apply)(pl, a)


if __name__ == "__main__":
    main()
