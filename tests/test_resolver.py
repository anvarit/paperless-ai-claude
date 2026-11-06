"""Tests for paperless_ai_claude.resolver.resolve().

Run:  python3 tests/test_resolver.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from paperless_ai_claude.resolver import normalise, resolve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(**overrides):
    """Build a Config by setting env vars, then override with **overrides."""
    for key in (
        "PAPERLESS_URL", "PAPERLESS_TOKEN", "ANTHROPIC_API_KEY",
        "PROCESSED_TAG", "REDO_TAG", "FAILED_TAG", "EXCLUDE_TAGS",
        "ALLOW_NEW_TAGS", "ALLOW_NEW_CORRESPONDENTS",
        "ALLOW_NEW_DOCUMENT_TYPES", "OVERWRITE_TITLE",
        "OVERWRITE_CORRESPONDENT", "OVERWRITE_DOCUMENT_TYPE",
        "OVERWRITE_DATE", "AMOUNT_FIELD", "TAG_CASE", "MAX_TAGS",
    ):
        os.environ.pop(key, None)
    os.environ["PAPERLESS_URL"] = "http://localhost"
    os.environ["PAPERLESS_TOKEN"] = "x"
    os.environ["ANTHROPIC_API_KEY"] = "sk-test"
    for k, v in overrides.items():
        os.environ[k] = str(v)
    from paperless_ai_claude.config import Config
    return Config()


def _doc(**extra):
    return {"title": "Old", "tags": [], "correspondent": None,
            "document_type": None, "created_date": None, "added": None,
            "custom_fields": [], **extra}


def _result(**extra):
    return type("Result", (), {
        "correspondent": extra.get("correspondent"),
        "document_type": extra.get("document_type"),
        "title": extra.get("title"),
        "document_date": extra.get("document_date"),
        "amount": extra.get("amount"),
        "tags": extra.get("tags", []),
    })()


def creator(kind, name, **extra):
    return {"id": 999, "name": name, **extra}


# ---------------------------------------------------------------------------
# 1. normalise(): case, punctuation, non-Latin
# ---------------------------------------------------------------------------

def test_normalise_case():
    assert normalise("HELLO") == "hello"
    assert normalise("HeLLo") == "hello"


def test_normalise_punctuation():
    assert normalise("hello-world") == "hello world"
    assert normalise("foo!@#$bar") == "foo bar"


def test_normalise_non_latin():
    # Non-Latin characters are not stripped by [^a-z0-9]+
    assert "汉" in normalise("汉语")
    assert normalise("日本語") == "日本語"


def test_normalise_empty():
    assert normalise("") == ""
    assert normalise(None) == ""


# ---------------------------------------------------------------------------
# 2. tag matching: case/punctuation-insensitive
# ---------------------------------------------------------------------------

def test_tag_match_case_insensitive():
    cfg = _cfg()
    tags = [{"id": 1, "name": "Workplace-Safety"}]
    res = _result(tags=["workplace-safety"])
    patch, notes = resolve(cfg, _doc(tags=[1]), res, tags, [], [], [], creator)
    assert 1 in patch.get("tags", [])


def test_tag_match_punctuation_insensitive():
    cfg = _cfg()
    tags = [{"id": 1, "name": "Workplace-Safety"}]
    res = _result(tags=["Workplace Safety"])
    patch, notes = resolve(cfg, _doc(tags=[1]), res, tags, [], [], [], creator)
    assert 1 in patch.get("tags", [])


# ---------------------------------------------------------------------------
# 3. unmatched tags: dropped when !ALLOW_NEW_TAGS, created when true
# ---------------------------------------------------------------------------

def test_unmatched_tag_dropped_when_no_new_tags():
    cfg = _cfg(ALLOW_NEW_TAGS=False)
    res = _result(tags=["Unknown"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, [], [], [], [], creator)
    assert "Unknown" not in [t for t in patch.get("tags", [])]
    assert any("dropped" in n for n in notes)


def test_unmatched_tag_created_when_allow_new():
    cfg = _cfg(allow_new_tags=True)
    res = _result(tags=["Unknown"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, [], [], [], [], creator)
    assert 999 in patch.get("tags", [])
    assert any("created" in n for n in notes)


# ---------------------------------------------------------------------------
# 4. reserved tags: never added; redo removed from document
# ---------------------------------------------------------------------------

def test_reserved_tag_not_added():
    """Excluded tags (processed/redo/failed/exclude/inbox) are never added."""
    cfg = _cfg()
    tags = [{"id": 1, "name": "ai-processed"}]
    res = _result(tags=["ai-processed"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, tags, [], [], [], creator)
    assert 1 not in patch.get("tags", [])
    assert any("reserved" in n for n in notes)


def test_redo_tag_removed_from_document():
    """The redo tag is removed from the document's existing tags."""
    cfg = _cfg()
    redo_id = 10
    tags = [{"id": 1, "name": "Invoice"}, {"id": redo_id, "name": "ai-redo"}]
    res = _result(tags=[])
    patch, notes = resolve(
        cfg, _doc(tags=[1, redo_id]), res, tags, [], [], [], creator)
    assert redo_id not in patch.get("tags", [])


def test_exclude_tag_not_added():
    """Tags listed in EXCLUDE_TAGS are never added."""
    cfg = _cfg(EXCLUDE_TAGS="spam")
    tags = [{"id": 1, "name": "spam"}]
    res = _result(tags=["spam"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, tags, [], [], [], creator)
    assert 1 not in patch.get("tags", [])


def test_inbox_tag_not_added():
    """Tags with is_inbox_tag=True are never added."""
    cfg = _cfg()
    tags = [{"id": 1, "name": "Inbox", "is_inbox_tag": True}]
    res = _result(tags=["Inbox"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, tags, [], [], [], creator)
    assert 1 not in patch.get("tags", [])


# ---------------------------------------------------------------------------
# 5. MAX_TAGS limits the number of tags
# ---------------------------------------------------------------------------

def test_max_tags_limit():
    cfg = _cfg(MAX_TAGS=2)
    tags = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]
    res = _result(tags=["A", "B", "C"])
    patch, notes = resolve(cfg, _doc(tags=[]), res, tags, [], [], [], creator)
    tag_count = len([t for t in patch.get("tags", []) if t not in (1, 2, 3)])
    assert tag_count <= 2  # Only up to max_tags new tags


# ---------------------------------------------------------------------------
# 6. correspondent: kept when doc already has one unless OVERWRITE
# ---------------------------------------------------------------------------

def test_correspondent_kept_when_existing():
    cfg = _cfg()
    res = _result(correspondent="Other")
    patch, notes = resolve(
        cfg, _doc(correspondent=50), res, [],
        [{"id": 50, "name": "A"}], [], [], creator)
    assert "correspondent" not in patch


def test_correspondent_set_when_none():
    cfg = _cfg(allow_new_correspondents=True)
    res = _result(correspondent="NewCo")
    patch, notes = resolve(
        cfg, _doc(correspondent=None), res, [],
        [], [], [], creator)
    assert patch.get("correspondent") == 999


# ---------------------------------------------------------------------------
# 7. document_type: matched case-insensitively; lowercased when TAG_CASE=lower
# ---------------------------------------------------------------------------

def test_document_type_case_insensitive():
    cfg = _cfg()
    types = [{"id": 1, "name": "Invoice"}]
    res = _result(document_type="INVOICE")
    patch, notes = resolve(
        cfg, _doc(document_type=None), res, [], [], types, [], creator)
    assert patch.get("document_type") == 1


def test_document_type_lowercased_when_tag_case_lower():
    cfg = _cfg(tag_case="lower")
    types = [{"id": 1, "name": "Invoice"}]
    res = _result(document_type="INVOICE")
    patch, notes = resolve(
        cfg, _doc(document_type=None), res, [], [], types, [], creator)
    assert patch.get("document_type") == 1


# ---------------------------------------------------------------------------
# 8. title: set only when OVERWRITE_TITLE and different
# ---------------------------------------------------------------------------

def test_title_set_when_overwrite_and_different():
    cfg = _cfg(overwrite_title=True)
    res = _result(title="New Title")
    patch, notes = resolve(
        cfg, _doc(title="Old"), res, [], [], [], [], creator)
    assert patch.get("title") == "New Title"


def test_title_not_set_when_not_overwrite():
    cfg = _cfg(overwrite_title=False)
    res = _result(title="New Title")
    patch, notes = resolve(
        cfg, _doc(title="Old"), res, [], [], [], [], creator)
    assert "title" not in patch


# ---------------------------------------------------------------------------
# 9. date: set only when created_date == added date, or OVERWRITE_DATE
# ---------------------------------------------------------------------------

def test_date_set_when_created_equals_added():
    cfg = _cfg()
    res = _result(document_date="2024-05-01")
    patch, notes = resolve(
        cfg, _doc(created_date="2024-05-01", added="2024-05-01"),
        res, [], [], [], [], creator)
    assert patch.get("created") == "2024-05-01"


def test_date_not_set_when_different_and_no_overwrite():
    cfg = _cfg()
    res = _result(document_date="2024-05-01")
    patch, notes = resolve(
        cfg, _doc(created_date="2023-01-01", added="2023-01-01"),
        res, [], [], [], [], creator)
    assert "created" not in patch


def test_invalid_date_ignored():
    cfg = _cfg()
    res = _result(document_date="2026-13-40")
    patch, notes = resolve(
        cfg, _doc(created_date="2024-05-01", added="2024-05-01"),
        res, [], [], [], [], creator)
    assert "created" not in patch


# ---------------------------------------------------------------------------
# 10. amount: written as float rounded to 2 decimals; nothing when empty/None
# ---------------------------------------------------------------------------

def test_amount_written_as_float():
    cfg = _cfg(AMOUNT_FIELD="amount")
    res = _result(amount=123.456)
    patch, notes = resolve(
        cfg, _doc(custom_fields=[]), res, [], [], [],
        [{"id": 1, "name": "amount"}], creator)
    assert patch.get("custom_fields") == [{"field": 1, "value": 123.46}]


def test_amount_not_written_when_field_empty():
    cfg = _cfg(AMOUNT_FIELD="")
    res = _result(amount=100.0)
    patch, notes = resolve(
        cfg, _doc(custom_fields=[]), res, [], [], [], [], creator)
    assert "custom_fields" not in patch


def test_amount_not_written_when_none():
    cfg = _cfg(AMOUNT_FIELD="amount")
    res = _result()
    patch, notes = resolve(
        cfg, _doc(custom_fields=[]), res, [], [], [],
        [{"id": 1, "name": "amount"}], creator)
    assert "custom_fields" not in patch


def test_amount_replaces_existing_field_keeps_others():
    cfg = _cfg(AMOUNT_FIELD="amount")
    res = _result(amount=50.0)
    patch, notes = resolve(
        cfg,
        _doc(custom_fields=[
            {"field": 1, "value": "old"},
            {"field": 2, "value": "keep"},
        ]),
        res, [], [], [],
        [{"id": 1, "name": "amount"}], creator)
    expected = [
        {"field": 2, "value": "keep"},
        {"field": 1, "value": 50.0},
    ]
    assert patch.get("custom_fields") == expected


def test_amount_zero_written():
    cfg = _cfg(AMOUNT_FIELD="amount")
    res = _result(amount=0.0)
    patch, notes = resolve(
        cfg, _doc(custom_fields=[]), res, [], [], [],
        [{"id": 1, "name": "amount"}], creator)
    assert patch.get("custom_fields") == [{"field": 1, "value": 0.0}]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all():
    for name, obj in sorted(globals().items()):
        if name.startswith("test_") and callable(obj):
            obj()
            print(f"ok  {name}")


if __name__ == "__main__":
    run_all()
