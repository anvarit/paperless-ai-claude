# paperless-ai-claude

Classify [Paperless-ngx](https://docs.paperless-ngx.com/) documents with Claude: title,
correspondent, document type, tags, date and an optional amount field, driven by a prompt you edit
in the browser. A replacement for the unmaintained [paperless-ai](https://github.com/clusterzx/paperless-ai).

- One Docker container: background worker + small web UI (queue, before/after history, re-run, dry-run, prompt editor, backups).
- Claude API with structured outputs and prompt caching; any Anthropic API key. Any OpenAI-compatible API works too (untested, see below).
- Keeps your tag vocabulary clean: existing tags first, creation opt-in, cleanup tools included.
- State lives in Paperless tags; no extra database to migrate.

Status: design complete ([PLAN.md](PLAN.md)), build not started.

## Quick start (once built)

```bash
cp .env.example .env   # PAPERLESS_URL, PAPERLESS_TOKEN, ANTHROPIC_API_KEY
docker compose -f docker-compose.example.yml up -d
open http://localhost:8720
```

## Other LLM providers

The project is tuned and tested on Claude. Any OpenAI-compatible chat API can be used instead with
`LLM_PROVIDER=openai`, but other models are untested: the prompt, structured output and tag
discipline may behave differently, and prompt caching (the `cache_read` / `cache_write` columns)
only applies to Anthropic. `OPENAI_API_KEY` is required but local servers ignore its value, so any
dummy string works there.

```bash
# OpenAI
LLM_PROVIDER=openai  OPENAI_API_KEY=sk-...  MODEL=<openai model id>
# Google Gemini (OpenAI-compatible endpoint)
LLM_PROVIDER=openai  OPENAI_API_KEY=...  OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/  MODEL=gemini-3.8-flash
# DeepSeek
LLM_PROVIDER=openai  OPENAI_API_KEY=...  OPENAI_BASE_URL=https://api.deepseek.com  MODEL=deepseek-flash
# Mistral
LLM_PROVIDER=openai  OPENAI_API_KEY=...  OPENAI_BASE_URL=https://api.mistral.ai/v1  MODEL=mistral-large-latest
# OpenRouter
LLM_PROVIDER=openai  OPENAI_API_KEY=sk-or-...  OPENAI_BASE_URL=https://openrouter.ai/api/v1  MODEL=<vendor>/<model>
# Local Ollama (from Docker use the host or container name instead of localhost)
LLM_PROVIDER=openai  OPENAI_API_KEY=ollama  OPENAI_BASE_URL=http://localhost:11434/v1  MODEL=<local model name>
```

MIT licence.
