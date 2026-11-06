# paperless-ai-claude — design

A self-hosted replacement for [paperless-ai](https://github.com/clusterzx/paperless-ai)
(unmaintained) that classifies [Paperless-ngx](https://docs.paperless-ngx.com/) documents with
Claude: title, correspondent, document type, tags, date and an optional amount field, driven by a
prompt you edit in the browser. One Docker container, configured by environment variables,
usable by anyone with a Paperless-ngx instance and an Anthropic API key.

Status: design complete, build not started.

## 1. Goals and non-goals

Goals
- Automatic, hands-off classification of new documents with **your** rules (title format,
  tag vocabulary, custom fields), not a fixed prompt.
- Keep the tag vocabulary clean: the model picks from existing tags by default; creation is opt-in.
- Reviewable: dry-run on any document, before/after history, one-click re-run, metadata backups
  before every bulk change.
- Cheap and predictable: structured outputs (no JSON parsing of free text), prompt caching,
  ~6–12k tokens per document.

Non-goals
- Chat / RAG over the archive. Use a Paperless MCP server with your Claude client instead
  (see §8), or Paperless-ngx 3.x's built-in Document Chat.
- Replacing Paperless-ngx's own classifier, OCR or workflows.
- Multi-user auth in the web UI (it is meant for a LAN or behind your reverse proxy).

## 2. Why not Paperless-ngx's built-in AI (3.x)?

Paperless-ngx 3.x can suggest title, date, tags, correspondent and type through an
OpenAI-compatible or Ollama backend and apply them via a workflow action. It has no custom prompt,
so house rules such as a fixed title format or a custom field cannot be expressed. If the default
suggestions are good enough for you, use that and skip this project. Note that its backend forces
tool calls, which Anthropic's OpenAI-compatible endpoint rejects on Claude Opus 5.5 / Sonnet 5.5
(use Haiku 5.5 or Sonnet 5 there).

## 3. Architecture

```
Paperless-ngx  ◄── REST (API token of a dedicated, non-superuser Paperless user)
      ▲
      │
┌─────┴──────────────────────────────────────────────────────────┐
│ paperless-ai-claude (one container)                             │
│  worker  every INTERVAL_MIN: queue → Claude API → resolve → PATCH│
│  web UI  queue · run history (before/after) · re-run · dry-run  │
│          · prompt editor · backups                              │
│  /data   runs.db (SQLite) · prompt.md · backups/                │
└────────────────────────────────────────────────────────────────┘
      │
      └── Claude API (official `anthropic` SDK, structured outputs, prompt caching)
```

Queue = documents that do not carry `PROCESSED_TAG`, plus documents that carry `REDO_TAG`.
State lives in Paperless tags only; the SQLite file holds run history and token usage.

Per document
1. `GET /api/documents/{id}/` → title, content, created, added, tags, correspondent,
   document_type, custom_fields. Empty text → tagged `FAILED_TAG` with reason "no text".
2. Prompt = `prompt.md` (your rules) + existing tag / correspondent / document-type names +
   the document text (cut to `MAX_CONTENT_CHARS`, head 80 % / tail 20 %) in a delimited block
   marked as data, not instructions. The static part carries `cache_control`.
3. `messages.parse(..., output_config={"format": schema})` → `{title, correspondent,
   document_type, tags[], document_date, language, amount, flags{}}`. No tools are exposed to
   the model, so a hostile scan can only skew its own metadata.
4. Resolve against Paperless: case/punctuation-insensitive match for tags, correspondent,
   type; create only where `ALLOW_NEW_*` permits, otherwise drop and record "unmatched".
   Amount → custom field named `AMOUNT_FIELD` (float), created if missing.
5. `PATCH /api/documents/{id}/` with changed fields only. Tags = existing ∪ resolved ∪
   {PROCESSED_TAG} − {REDO_TAG}. Existing correspondent kept unless `OVERWRITE_CORRESPONDENT`.
   `created` only set when Paperless had no better value than the added date.
6. Row in `runs.db`: document id, timestamp, before, after, model, usage (input, output,
   cache read), duration, status, error. One JSON log line to stdout.

Failure handling: a document that fails three runs gets `FAILED_TAG` and leaves the queue;
remove the tag (or press re-run) to retry. API errors back off until the next interval.
`stop_reason: refusal` is recorded as a failure with the category.

## 4. Web UI

FastAPI + Jinja2 + htmx, no build step, served on `PORT` (default 8720).

- `/` queue size, last runs, failures, token usage today / this month.
- `/runs/{id}` before/after diff, raw model output, re-run and dry-run buttons.
- `/documents/{id}` dry-run any Paperless document by id.
- `/prompt` edit `prompt.md` in the browser; saved to the volume, used from the next run.
- `/backups` list snapshots, create one, restore one (all or a single document).
- `/settings` effective configuration, secrets masked.
- `/api/status` JSON for monitoring.

No authentication: bind to a LAN address or put it behind your reverse proxy's auth.

## 5. Configuration (all environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `PAPERLESS_URL` | required | e.g. `http://paperless:8000` |
| `PAPERLESS_PUBLIC_URL` | = `PAPERLESS_URL` | browser-reachable Paperless address for links in the UI |
| `PAPERLESS_TOKEN` | required | API token of a dedicated Paperless user (view/change documents, add tags/correspondents/types/custom fields) |
| `ANTHROPIC_API_KEY` | required | Claude API key (Max and Team plans include monthly API credits) |
| `MODEL` | `claude-sonnet-5-5` | any Claude model; `claude-haiku-5-5` for the cheapest option |
| `PROCESSED_TAG` / `REDO_TAG` / `FAILED_TAG` | `ai-processed` / `ai-redo` / `ai-failed` | workflow tags, created on first run, never offered to the model |
| `EXCLUDE_TAGS` | empty | comma-separated tags never offered to the model nor changed (inbox tags, folder tags) |
| `ALLOW_NEW_TAGS` | `false` | let the model create tags |
| `ALLOW_NEW_CORRESPONDENTS` | `true` | |
| `ALLOW_NEW_DOCUMENT_TYPES` | `false` | |
| `OVERWRITE_TITLE` | `true` | |
| `OVERWRITE_CORRESPONDENT` | `false` | |
| `AMOUNT_FIELD` | empty | name of a float custom field to fill for invoices/receipts; empty = off |
| `TAG_CASE` | `lower` | `lower`, `keep` |
| `MAX_TAGS` | `5` | |
| `QUEUE_SINCE` | empty | the periodic loop only takes documents added after this date (YYYY-MM-DD); older ones are left for `backfill` |
| `BATCH_SIZE` | `20` | documents per run |
| `INTERVAL_MIN` | `10` | worker interval; `0` = run once and exit (cron mode) |
| `MAX_CONTENT_CHARS` | `20000` | |
| `BACKUP_DIR` | `/data/backups` | metadata snapshots (see §6); mount a share here if you want them off-box |
| `PORT` | `8720` | web UI |
| `LOG_FORMAT` | `json` | `json` or `text` |

Secrets are never written to disk by the app and never appear in the UI or logs.

## 6. Backups and restore

Before every bulk operation (tag cleanup, backfill batch, restore) and on demand, the app writes
`BACKUP_DIR/<YYYYMMDD-HHMM>-<reason>/` with `documents.json` (id, title, created, tags,
correspondent, document_type, custom_fields), `tags.json`, `correspondents.json`,
`document_types.json`, fetched through the API. `restore --from <dir> [--doc <id>]` puts that
metadata back. Document files are never touched by this app; keep using Paperless's
`document_exporter` for real backups.

## 7. Tag policy (default prompt)

- Correspondent = sender, document type = kind of paper, tags = cross-cutting topics shared by
  many documents (house, car, health, insurance, business, warranty, action needed). Facts true
  for one document only (amounts, invoice numbers, dates, sender) are not tags; they go to the
  title, the date, the correspondent or the amount field.
- One language and one casing for tags (`TAG_CASE`), 1–`MAX_TAGS` per document, existing tags
  first. Unmatched suggestions are listed in the UI instead of silently creating tags.
- `tools/cleanup_tags.py` helps repair an existing sprawl: dry-run list → delete tags that are
  bare numbers/amounts, merge case and punctuation variants, export candidate synonym pairs as
  CSV for a human decision, delete tags with zero documents. Writes a backup first.
- `tools/vocabulary.py` drafts a starting vocabulary from the surviving tags (grouped, with
  document counts) for pruning in the Paperless UI before a backfill.

The shipped `prompt.md` encodes these rules plus placeholders for your own (title format,
business/private rules, languages seen). Edit it in the UI.

## 8. Asking questions about your archive

Not part of this app. Run a Paperless MCP server (several open-source ones exist on GitHub /
npm / PyPI; pick one that supports search, get document, list tags/correspondents, and ideally a
read-only token) and connect it to Claude (claude.ai connectors, Claude Desktop, Claude Code).
Paperless's full-text search is good enough that no vector index is needed.

## 9. Repository layout (planned)

```
paperless_ai_claude/   app package: paperless.py (client), claude.py (parse call), resolver.py, worker.py, web/ (FastAPI, templates)
tools/                 cleanup_tags.py, vocabulary.py, backfill.py, restore.py
prompt.md              default prompt
schema.json            structured output schema
tests/                 test_resolver.py (matching/normalisation), test_prompt.py
Dockerfile             python:3.13-slim + anthropic fastapi uvicorn jinja2 httpx
docker-compose.example.yml
.env.example
LICENSE                MIT
```

## 10. Build phases

1. **Prep (your instance):** dedicated Paperless user + token, API key, metadata backup, tag
   cleanup with `tools/cleanup_tags.py`, draft vocabulary, prune it in Paperless.
2. **Build:** Paperless client, Claude call, resolver with tests, worker, web UI, Docker image.
   Dry-run on ~20 documents (half already classified by hand or by the old tool, half new),
   tune `prompt.md`, compare two models on the same set.
3. **Backfill:** `tools/backfill.py --dry-run` → CSV review → batches of 100, oldest last.
4. **Cutover:** stop paperless-ai (keep its volume for 30 days), rotate the Paperless token it
   used, remove its reverse-proxy entries.
5. **Optional:** Paperless MCP server; notification of daily counts through your own channel;
   monitoring on `/api/status`.

## 11. Verification

```bash
docker logs --tail 50 paperless-ai-claude
curl -s http://<host>:8720/api/status | jq
curl -s -H "Authorization: Token $T" "$PAPERLESS_URL/api/documents/?tags__name__iexact=ai-processed&page_size=1" | jq .count
curl -s -H "Authorization: Token $T" "$PAPERLESS_URL/api/tags/?page_size=1" | jq .count   # should stay flat
curl -s -H "Authorization: Token $T" "$PAPERLESS_URL/api/documents/<id>/history/" | jq '.[0]'
```

## 12. Symptom → cause

| Symptom | Cause |
|---|---|
| `AuthenticationError` 401 from Claude | wrong or revoked `ANTHROPIC_API_KEY`, or a workspace without credits |
| `RateLimitError` 429 / credits exhausted | monthly allowance or org rate limit; the worker backs off to the next interval |
| `stop_reason: refusal` | safety classifier declined that document; shown in the run, document tagged `FAILED_TAG` |
| Cache reads stay 0 in usage | the static prefix changes every call (unsorted lists, timestamps in the prompt) |
| Amount-like tags appear | a prompt asks for amounts while `AMOUNT_FIELD` is empty |
| Document never processed | empty OCR text, or `FAILED_TAG` after three failures |
| Correspondent unchanged | existing correspondent kept (`OVERWRITE_CORRESPONDENT=false`) |
| 403 from Paperless on PATCH | the API user lacks change permission or does not own the document |
