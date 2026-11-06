"""Same interface as Claude, for any OpenAI-compatible chat endpoint (OpenAI, Gemini, DeepSeek, Mistral, OpenRouter, Ollama, ...)."""
import time

import openai

from .claude import Classification, Refused


class OpenAICompat:
    def __init__(self, api_key, model, base_url=None, max_retries=2, timeout=120):
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url or None, max_retries=max_retries, timeout=timeout)
        self.model = model

    def classify(self, system, user_text):
        if not isinstance(system, str):                      # Anthropic block list from build_system -> plain text
            system = "\n".join(b["text"] for b in system)
        t0 = time.time()
        resp = self.client.chat.completions.parse(
            model=self.model, max_completion_tokens=2048,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_text}],
            response_format=Classification,
        )
        msg = resp.choices[0].message
        if msg.refusal:
            raise Refused(f"refused ({msg.refusal})")
        u = resp.usage
        details = getattr(u, "prompt_tokens_details", None)
        usage = {"input": u.prompt_tokens, "output": u.completion_tokens,
                 "cache_read": getattr(details, "cached_tokens", 0) or 0,
                 "cache_write": getattr(details, "cache_write_tokens", 0) or 0,
                 "model": resp.model, "duration_ms": int((time.time() - t0) * 1000)}
        return msg.parsed, usage
