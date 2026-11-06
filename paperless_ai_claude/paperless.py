"""Minimal Paperless-ngx REST client (standard library only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class PaperlessError(RuntimeError):
    pass


class Paperless:
    def __init__(self, url, token, timeout=60):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def req(self, method, path, data=None, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(self.url + path + q, data=body, method=method,
                                   headers={"Authorization": f"Token {self.token}", "Content-Type": "application/json",
                                            "Accept": "application/json; version=9"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise PaperlessError(f"{method} {path}{q} -> {e.code} {e.read()[:300]!r}") from None
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise PaperlessError(f"{method} {path}{q} -> {e}") from None

    def all(self, path, params=None):
        params = dict(params or {}, page_size=100, page=1)
        while True:
            page = self.req("GET", path, params=params)
            yield from page["results"]
            if not page.get("next"):
                return
            params["page"] += 1

    def count(self, path, params=None):
        return self.req("GET", path, params=dict(params or {}, page_size=1))["count"]

    # --- metadata objects -------------------------------------------------
    def tags(self):
        return list(self.all("/api/tags/", {"fields": "id,name,document_count,is_inbox_tag"}))

    def correspondents(self):
        return list(self.all("/api/correspondents/", {"fields": "id,name,document_count"}))

    def document_types(self):
        return list(self.all("/api/document_types/", {"fields": "id,name,document_count"}))

    def custom_fields(self):
        return list(self.all("/api/custom_fields/"))

    def create(self, kind, name, **extra):
        """kind: tags | correspondents | document_types | custom_fields. Created without owner so every user sees it."""
        body = {"name": name, "owner": None}
        if kind != "custom_fields":
            body["matching_algorithm"] = 0
        body.update(extra)
        return self.req("POST", f"/api/{kind}/", body)

    # --- documents ----------------------------------------------------------
    DOC_FIELDS = "id,title,content,created,created_date,added,tags,correspondent,document_type,custom_fields,original_file_name"

    def document(self, doc_id, with_content=True):
        fields = self.DOC_FIELDS if with_content else self.DOC_FIELDS.replace("content,", "")
        return self.req("GET", f"/api/documents/{doc_id}/", params={"fields": fields})

    def documents(self, params):
        return self.all("/api/documents/", dict(params, fields=self.DOC_FIELDS.replace("content,", "")))

    def update_document(self, doc_id, patch):
        return self.req("PATCH", f"/api/documents/{doc_id}/", patch)

    def history(self, doc_id):
        return self.req("GET", f"/api/documents/{doc_id}/history/")
