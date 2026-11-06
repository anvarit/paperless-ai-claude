"""In-memory stand-ins for Paperless and Claude used by the tests."""
import copy
from paperless_ai_claude.claude import Classification


class FakePaperless:
    def __init__(self, docs, tags=(), correspondents=(), types=(), custom_fields=()):
        self.docs = {d["id"]: copy.deepcopy(d) for d in docs}
        self.objs = {"tags": [dict(t) for t in tags], "correspondents": [dict(c) for c in correspondents],
                     "document_types": [dict(t) for t in types], "custom_fields": [dict(f) for f in custom_fields]}
        self.patches = []
        self.next_id = 1000

    def tags(self): return [dict(t, document_count=0, is_inbox_tag=t.get("is_inbox_tag", False)) for t in self.objs["tags"]]
    def correspondents(self): return [dict(c) for c in self.objs["correspondents"]]
    def document_types(self): return [dict(t) for t in self.objs["document_types"]]
    def custom_fields(self): return [dict(f) for f in self.objs["custom_fields"]]

    def create(self, kind, name, **extra):
        self.next_id += 1
        obj = {"id": self.next_id, "name": name, **extra}
        self.objs[kind].append(obj)
        return dict(obj)

    def document(self, doc_id, with_content=True):
        return copy.deepcopy(self.docs[doc_id])

    def documents(self, params):
        none = {int(x) for x in str(params.get("tags__id__none", "")).split(",") if x}
        all_ = params.get("tags__id__all")
        for d in sorted(self.docs.values(), key=lambda d: d["added"], reverse=True):
            tags = set(d.get("tags") or [])
            if all_ is not None and int(all_) not in tags:
                continue
            if tags & none:
                continue
            if "added__date__lt" in params and d["added"][:10] >= params["added__date__lt"]:
                continue
            yield copy.deepcopy(d)

    def count(self, path, params=None):
        return sum(1 for _ in self.documents(params or {}))

    def all(self, path, params=None):
        if path == "/api/documents/":
            yield from self.documents(params or {})
        else:
            yield from self.objs[path.strip("/").split("/")[-1]]

    def update_document(self, doc_id, patch):
        self.patches.append((doc_id, copy.deepcopy(patch)))
        d = self.docs[doc_id]
        for k, v in patch.items():
            d["created_date" if k == "created" else k] = v
        return copy.deepcopy(d)


class FakeClaude:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def classify(self, system, user_text):
        self.calls.append((system, user_text))
        if self.error:
            raise self.error
        r = self.result if isinstance(self.result, Classification) else Classification(**{"readable": True, **self.result})
        return r, {"input": 1200, "output": 90, "cache_read": 4000, "cache_write": 0, "model": "fake", "duration_ms": 5}
