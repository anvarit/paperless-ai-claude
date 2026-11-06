#!/usr/bin/env python3
"""Correspondent hygiene for Paperless-ngx: merge spelling variants, drop placeholders, delete unused ones.

  cleanup_correspondents.py plan  --out DIR [--rules rules.csv]   write DIR/correspondents.csv and DIR/plan.json
  cleanup_correspondents.py apply --plan DIR/plan.json            back up, then merge / rename / reset / delete

Automatic grouping: names that are equal after casefolding, accent and punctuation stripping and removal of
legal-form suffixes (NV, BV, BVBA, SA, SARL, GmbH, Ltd, LLC, Inc, vzw, ...) are merged into the variant with
the most documents (ties: the better-cased name). rules.csv rows "target,pattern[,pattern...]" (case-insensitive
regexes on the whole name, first matching row wins) override the grouping: the target becomes the resulting
name (an existing correspondent of that name is reused, else the biggest member is renamed). Target "-" resets:
the documents lose their correspondent and get the redo tag so the classifier proposes a fresh one, then the
correspondent is deleted. Correspondents with zero documents are deleted.
Environment: PAPERLESS_URL, PAPERLESS_TOKEN (document edits), PAPERLESS_ADMIN_TOKEN (deletes and renames; defaults
to PAPERLESS_TOKEN, but only the owner or a superuser may delete), REDO_TAG (ai-redo), BACKUP_DIR (./backups).
"""
import argparse, csv, json, os, re, sys, unicodedata
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from cleanup_tags import Paperless
from vocabulary import load_vocab

SUFFIX = re.compile(r"\b(nv|sa|bv|bvba|sprl|srl|sarl|sas|gmbh|ag|llc|ltd|inc|plc|vzw|asbl|cv|cvba|comm ?v|co|kg|se|oy|ab|aps|a s)\b")


def norm(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", SUFFIX.sub(" ", s)).strip()


def cased(name):
    return sum(c.islower() for c in name)


def plan(cs, rules):
    merges, renames, resets, deletes = [], [], [], []
    groups, hits = {}, {}
    for c in cs:
        if c["document_count"] == 0:
            deletes.append({"id": c["id"], "name": c["name"], "documents": 0, "why": "empty"}); continue
        hit = next((t for t, pats in rules if any(p.fullmatch(c["name"]) for p in pats)), None)
        key = norm(c["name"])
        if hit == "-" or (hit is None and not key):
            resets.append({"id": c["id"], "name": c["name"], "documents": c["document_count"]}); continue
        hits[c["id"]] = hit
        groups.setdefault(("rule", hit) if hit else ("auto", key), []).append(c)
    for (kind, key), ms in groups.items():
        ms.sort(key=lambda c: (-c["document_count"], -cased(c["name"])))
        if kind == "auto":
            if len(ms) == 1:
                continue
            tgt = ms[0]
        else:
            tgt = next((c for c in cs if c["name"].casefold() == key.casefold() and hits.get(c["id"]) == key), None) or ms[0]
            if tgt["name"] != key:
                renames.append({"id": tgt["id"], "from": tgt["name"], "to": key})
        for c in ms:
            if c["id"] != tgt["id"]:
                merges.append({"from": c["id"], "from_name": c["name"], "documents": c["document_count"], "into": tgt["id"], "into_name": key if kind == "rule" else tgt["name"]})
    return {"merge": merges, "rename": renames, "reset": resets, "delete": deletes}


def cmd_plan(pl, a):
    cs = list(pl.all("/api/correspondents/", {"fields": "id,name,document_count", "ordering": "name"}))
    p = plan(cs, load_vocab(a.rules) if a.rules else [])
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "correspondents.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "name", "documents"]); [w.writerow([c["id"], c["name"], c["document_count"]]) for c in cs]
    json.dump(p, open(os.path.join(a.out, "plan.json"), "w"), indent=1, ensure_ascii=False)
    summary(cs, p)


def summary(cs, p):
    print(f'correspondents: {len(cs)}  merges: {len(p["merge"])}  renames: {len(p["rename"])}  resets: {len(p["reset"])} ({sum(r["documents"] for r in p["reset"])} docs)  deletes: {len(p["delete"])}')
    by = {}
    for m in p["merge"]:
        by.setdefault(m["into_name"], []).append(f'{m["from_name"]}:{m["documents"]}')
    for into, ms in sorted(by.items(), key=lambda kv: kv[0].casefold()):
        print(f"  {into}  <-  " + "; ".join(ms))
    print("  renames: " + "; ".join(f'{r["from"]!r}->{r["to"]!r}' for r in p["rename"]))
    print("  resets: " + "; ".join(f'{r["name"]}:{r["documents"]}' for r in sorted(p["reset"], key=lambda r: -r["documents"])))


def docs_of(pl, cid):
    return [d["id"] for d in pl.all("/api/documents/", {"correspondent__id": cid, "fields": "id"})]


def bulk(pl, ids, method, **params):
    for i in range(0, len(ids), 500):
        pl.req("POST", "/api/documents/bulk_edit/", {"documents": ids[i:i + 500], "method": method, "parameters": params})


def cmd_apply(pl, admin, a):
    p = json.load(open(a.plan))
    d = os.path.join(os.environ.get("BACKUP_DIR", "./backups"), datetime.now().strftime("%Y%m%d-%H%M") + "-correspondent-cleanup")
    os.makedirs(d, exist_ok=True)
    json.dump(list(pl.all("/api/correspondents/", {"fields": "id,name,document_count"})), open(os.path.join(d, "correspondents.json"), "w"), ensure_ascii=False)
    touched = [x["from"] for x in p["merge"]] + [x["id"] for x in p["reset"]]
    json.dump(list(pl.all("/api/documents/", {"correspondent__id__in": ",".join(map(str, touched)), "fields": "id,correspondent,tags"})) if touched else [],
              open(os.path.join(d, "documents.json"), "w"))
    print("backup written to", d)
    redo = os.environ.get("REDO_TAG", "ai-redo")
    redo_id = next((t["id"] for t in pl.all("/api/tags/", {"name__iexact": redo, "fields": "id,name"}) if t["name"].casefold() == redo.casefold()), None)
    if p["reset"] and redo_id is None:
        redo_id = pl.req("POST", "/api/tags/", {"name": redo, "matching_algorithm": 0, "owner": None})["id"]; print(f"created tag {redo!r}")
    for m in p["merge"]:
        ids = docs_of(pl, m["from"]); bulk(pl, ids, "set_correspondent", correspondent=m["into"])
        admin.req("DELETE", f"/api/correspondents/{m['from']}/"); print(f"merged {m['from_name']!r} -> {m['into_name']!r} ({len(ids)} docs)")
    for r in p["rename"]:
        admin.req("PATCH", f"/api/correspondents/{r['id']}/", {"name": r["to"]}); print(f"renamed {r['from']!r} -> {r['to']!r}")
    for r in p["reset"]:
        ids = docs_of(pl, r["id"]); bulk(pl, ids, "set_correspondent", correspondent=None); bulk(pl, ids, "modify_tags", add_tags=[redo_id], remove_tags=[])
        admin.req("DELETE", f"/api/correspondents/{r['id']}/"); print(f"reset {r['name']!r} ({len(ids)} docs -> no correspondent, tagged {redo})")
    for x in p["delete"]:
        admin.req("DELETE", f"/api/correspondents/{x['id']}/")
    print(f'done: {len(p["merge"])} merges, {len(p["rename"])} renames, {len(p["reset"])} resets, {len(p["delete"])} deletes')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("--out", required=True); s.add_argument("--rules")
    s = sub.add_parser("apply"); s.add_argument("--plan", required=True)
    a = ap.parse_args()
    url, token = os.environ.get("PAPERLESS_URL"), os.environ.get("PAPERLESS_TOKEN")
    if not url or not token:
        sys.exit("set PAPERLESS_URL and PAPERLESS_TOKEN")
    pl = Paperless(url, token)
    if a.cmd == "plan":
        cmd_plan(pl, a)
    else:
        cmd_apply(pl, Paperless(url, os.environ.get("PAPERLESS_ADMIN_TOKEN") or token), a)


if __name__ == "__main__":
    main()
