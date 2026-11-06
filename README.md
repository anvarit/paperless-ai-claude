# paperless-ai-claude

Classify [Paperless-ngx](https://docs.paperless-ngx.com/) documents with Claude: title,
correspondent, document type, tags, date and an optional amount field, driven by a prompt you edit
in the browser. A replacement for the unmaintained [paperless-ai](https://github.com/clusterzx/paperless-ai).

- One Docker container: background worker + small web UI (queue, before/after history, re-run, dry-run, prompt editor, backups).
- Claude API with structured outputs and prompt caching; any Anthropic API key.
- Keeps your tag vocabulary clean: existing tags first, creation opt-in, cleanup tools included.
- State lives in Paperless tags; no extra database to migrate.

Status: design complete ([PLAN.md](PLAN.md)), build not started.

## Quick start (once built)

```bash
cp .env.example .env   # PAPERLESS_URL, PAPERLESS_TOKEN, ANTHROPIC_API_KEY
docker compose -f docker-compose.example.yml up -d
open http://localhost:8720
```

MIT licence.
