#!/usr/bin/env python3
"""Map an existing tag list onto a curated vocabulary. Standard library only.

  vocabulary.py plan --tags DIR/tags.csv --vocab vocab.csv --out DIR [--delete-below 3]

vocab.csv rows: target,pattern[,pattern...]  (patterns are case-insensitive regexes matched
against the whole tag name; first matching row wins; a row with target only and pattern "^target$"
keeps that tag as is; target "-" deletes the matching tags whatever their count). Tags matching no row are deleted when they have fewer than --delete-below
documents, otherwise listed in DIR/review.csv for a human decision.

Writes DIR/vocab-plan.json (merge/delete/rename steps for cleanup_tags.py apply) and
DIR/vocab-summary.csv (target, resulting document count, merged tag count).
"""
import argparse, csv, json, os, re
from collections import defaultdict


def load_vocab(path):
    rows = []
    for r in csv.reader(open(path)):
        if not r or r[0].startswith("#"):
            continue
        target, pats = r[0].strip(), [p.strip() for p in r[1:] if p.strip()]
        rows.append((target, [re.compile(p, re.I) for p in pats] or [re.compile("^" + re.escape(target) + "$", re.I)]))
    return rows


def plan(tags, vocab, exclude, delete_below):
    merges, deletes, renames, review = [], [], [], []
    by_target = defaultdict(list)
    existing = {t["name"].casefold(): t for t in tags}
    hits = {}
    for t in tags:
        if t["name"] in exclude or t["class"] in ("amount", "empty", "variant"):
            continue
        hit = hits[t["id"]] = next((target for target, pats in vocab if any(p.fullmatch(t["name"]) for p in pats)), None)
        if hit is None:
            (deletes if t["documents"] < delete_below else review).append(t)
            continue
        if hit == "-":          # explicit delete row (document-type synonyms, payment methods, places, people)
            deletes.append(t)
            continue
        by_target[hit].append(t)
    for target, members in by_target.items():
        tgt = existing.get(target.casefold())
        if tgt and hits.get(tgt["id"]) != target:   # same name, but mapped elsewhere (e.g. "Statement" -> bank statement)
            tgt = None
        if tgt and tgt["name"] != target:
            renames.append({"id": tgt["id"], "from": tgt["name"], "to": target})
        for t in members:
            if tgt and t["id"] == tgt["id"]:
                continue
            merges.append({"from": t["id"], "from_name": t["name"], "into": tgt["id"] if tgt else None, "into_name": target})
    return merges, deletes, renames, review, by_target


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan"]); ap.add_argument("--tags", required=True); ap.add_argument("--vocab", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--delete-below", type=int, default=3)
    a = ap.parse_args()
    tags = [dict(r, id=int(r["id"]), documents=int(r["documents"])) for r in csv.DictReader(open(a.tags))]
    exclude = {s.strip() for s in os.environ.get("EXCLUDE_TAGS", "ai-processed,ai-redo,ai-failed").split(",") if s.strip()}
    merges, deletes, renames, review, by_target = plan(tags, load_vocab(a.vocab), exclude, a.delete_below)
    os.makedirs(a.out, exist_ok=True)
    json.dump({"merge": merges, "delete": [{"id": t["id"], "name": t["name"], "documents": t["documents"], "why": "not in vocabulary"} for t in deletes],
               "rename": renames}, open(os.path.join(a.out, "vocab-plan.json"), "w"), indent=1, ensure_ascii=False)
    with open(os.path.join(a.out, "vocab-summary.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["target", "documents_max", "merged_tags", "members"])
        for target, members in sorted(by_target.items(), key=lambda kv: -sum(m["documents"] for m in kv[1])):
            w.writerow([target, sum(m["documents"] for m in members), len(members), "; ".join(f'{m["name"]}:{m["documents"]}' for m in sorted(members, key=lambda m: -m["documents"]))])
    with open(os.path.join(a.out, "review.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "name", "documents"])
        for t in sorted(review, key=lambda t: -t["documents"]): w.writerow([t["id"], t["name"], t["documents"]])
    print(f"targets: {len(by_target)}  merges: {len(merges)}  renames: {len(renames)}  deletes(<{a.delete_below} docs, unmatched): {len(deletes)}  review: {len(review)}")


if __name__ == "__main__":
    main()
