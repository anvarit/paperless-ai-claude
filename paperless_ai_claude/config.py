"""Configuration from environment variables (see PLAN.md §5)."""
import os
from dataclasses import dataclass, field


def _bool(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name, default=""):
    return [s.strip() for s in os.environ.get(name, default).split(",") if s.strip()]


@dataclass
class Config:
    paperless_url: str = field(default_factory=lambda: os.environ.get("PAPERLESS_URL", "").rstrip("/"))
    paperless_token: str = field(default_factory=lambda: os.environ.get("PAPERLESS_TOKEN", ""))
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.environ.get("MODEL", "claude-sonnet-5-5"))
    processed_tag: str = field(default_factory=lambda: os.environ.get("PROCESSED_TAG", "ai-processed"))
    redo_tag: str = field(default_factory=lambda: os.environ.get("REDO_TAG", "ai-redo"))
    failed_tag: str = field(default_factory=lambda: os.environ.get("FAILED_TAG", "ai-failed"))
    exclude_tags: list = field(default_factory=lambda: _list("EXCLUDE_TAGS"))
    allow_new_tags: bool = field(default_factory=lambda: _bool("ALLOW_NEW_TAGS", False))
    allow_new_correspondents: bool = field(default_factory=lambda: _bool("ALLOW_NEW_CORRESPONDENTS", True))
    allow_new_document_types: bool = field(default_factory=lambda: _bool("ALLOW_NEW_DOCUMENT_TYPES", False))
    overwrite_title: bool = field(default_factory=lambda: _bool("OVERWRITE_TITLE", True))
    overwrite_correspondent: bool = field(default_factory=lambda: _bool("OVERWRITE_CORRESPONDENT", False))
    overwrite_document_type: bool = field(default_factory=lambda: _bool("OVERWRITE_DOCUMENT_TYPE", True))
    overwrite_date: bool = field(default_factory=lambda: _bool("OVERWRITE_DATE", False))
    amount_field: str = field(default_factory=lambda: os.environ.get("AMOUNT_FIELD", ""))
    tag_case: str = field(default_factory=lambda: os.environ.get("TAG_CASE", "lower"))
    max_tags: int = field(default_factory=lambda: int(os.environ.get("MAX_TAGS", "5")))
    batch_size: int = field(default_factory=lambda: int(os.environ.get("BATCH_SIZE", "20")))
    interval_min: int = field(default_factory=lambda: int(os.environ.get("INTERVAL_MIN", "10")))
    max_content_chars: int = field(default_factory=lambda: int(os.environ.get("MAX_CONTENT_CHARS", "20000")))
    max_failures: int = field(default_factory=lambda: int(os.environ.get("MAX_FAILURES", "3")))
    data_dir: str = field(default_factory=lambda: os.environ.get("DATA_DIR", "/data"))
    backup_dir: str = field(default_factory=lambda: os.environ.get("BACKUP_DIR", ""))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8720")))
    log_format: str = field(default_factory=lambda: os.environ.get("LOG_FORMAT", "json"))

    def __post_init__(self):
        if not self.backup_dir:
            self.backup_dir = os.path.join(self.data_dir, "backups")

    @property
    def prompt_path(self):
        return os.path.join(self.data_dir, "prompt.md")

    @property
    def db_path(self):
        return os.path.join(self.data_dir, "runs.db")

    @property
    def workflow_tags(self):
        return {self.processed_tag, self.redo_tag, self.failed_tag}

    def check(self):
        missing = [n for n, v in (("PAPERLESS_URL", self.paperless_url), ("PAPERLESS_TOKEN", self.paperless_token), ("ANTHROPIC_API_KEY", self.anthropic_api_key)) if not v]
        if missing:
            raise SystemExit("missing environment variables: " + ", ".join(missing))

    def public(self):
        d = self.__dict__.copy()
        for k in ("paperless_token", "anthropic_api_key"):
            d[k] = "***" if d[k] else ""
        return d
