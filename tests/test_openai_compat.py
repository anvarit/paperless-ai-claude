import os, sys, types, unittest
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from paperless_ai_claude.claude import Classification, Refused, build_system, make_client
from paperless_ai_claude.config import Config
from paperless_ai_claude.openai_compat import OpenAICompat

RESULT = Classification(readable=True, title="Invoice", correspondent="Acme", document_type="invoice", tags=["telecom"],
                        document_date="2026-10-12", language="en", amount=79.0)
ENV = {"PAPERLESS_URL": "http://x", "PAPERLESS_TOKEN": "t"}


def ns(**kw):
    return types.SimpleNamespace(**kw)


class FakeOpenAI:
    """Just enough of the SDK: client.chat.completions.parse(**kw) -> completion."""
    def __init__(self, parsed=RESULT, refusal=None, cached=None):
        self.calls = []
        details = ns(cached_tokens=cached) if cached is not None else None
        self.completion = ns(model="fake-1", choices=[ns(message=ns(parsed=parsed, refusal=refusal))],
                             usage=ns(prompt_tokens=1200, completion_tokens=90, prompt_tokens_details=details))
        self.chat = ns(completions=ns(parse=self.parse))

    def parse(self, **kw):
        self.calls.append(kw)
        return self.completion


def make(**fake):
    c = OpenAICompat("k", "m", base_url="http://localhost:1/v1")
    c.client = FakeOpenAI(**fake)
    return c


class OpenAICompatTest(unittest.TestCase):
    def test_happy_path(self):
        c = make(cached=1000)
        result, usage = c.classify("rules", "doc text")
        self.assertIs(result, RESULT)
        self.assertEqual(usage, {"input": 1200, "output": 90, "cache_read": 1000, "cache_write": 0, "model": "fake-1", "duration_ms": usage["duration_ms"]})
        kw, = c.client.calls
        self.assertEqual(kw["model"], "m")
        self.assertIs(kw["response_format"], Classification)
        self.assertEqual(kw["messages"], [{"role": "system", "content": "rules"}, {"role": "user", "content": "doc text"}])

    def test_no_cache_details(self):
        _, usage = make().classify("rules", "x")
        self.assertEqual((usage["cache_read"], usage["cache_write"]), (0, 0))

    def test_refusal(self):
        with self.assertRaises(Refused):
            make(parsed=None, refusal="no").classify("rules", "x")

    def test_system_blocks_flattened(self):
        c = make()
        c.classify(build_system("RULES", ["b", "a"], [], ["invoice"], []) + [{"type": "text", "text": "extra"}], "x")
        system = c.client.calls[0]["messages"][0]["content"]
        self.assertTrue(system.startswith("RULES\n\n## Existing tags (choose only from these)\na, b"))
        self.assertTrue(system.endswith("\nextra"))
        self.assertNotIn("cache_control", system)


class ConfigProviderTest(unittest.TestCase):
    def test_anthropic_default_needs_anthropic_key(self):
        with patch.dict(os.environ, ENV, clear=True):
            with self.assertRaisesRegex(SystemExit, "ANTHROPIC_API_KEY"):
                Config().check()
        with patch.dict(os.environ, {**ENV, "ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": ""}, clear=True):
            cfg = Config()
            cfg.check()
            self.assertEqual(cfg.llm_provider, "anthropic")

    def test_openai_needs_openai_key_only(self):
        with patch.dict(os.environ, {**ENV, "LLM_PROVIDER": "openai"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "OPENAI_API_KEY"):
                Config().check()
        with patch.dict(os.environ, {**ENV, "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "ollama", "OPENAI_BASE_URL": "http://localhost:11434/v1"}, clear=True):
            cfg = Config()
            cfg.check()
            self.assertEqual(cfg.public()["openai_api_key"], "***")
            self.assertIsInstance(make_client(cfg), OpenAICompat)

    def test_unknown_provider(self):
        with patch.dict(os.environ, {**ENV, "LLM_PROVIDER": "gemini", "OPENAI_API_KEY": "k"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "LLM_PROVIDER"):
                Config().check()


if __name__ == "__main__":
    unittest.main()
