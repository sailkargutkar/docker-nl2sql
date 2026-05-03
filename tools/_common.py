"""
Shared infrastructure for the LLM-powered build tools.

Responsibilities:
  - Load OPENAI_API_KEY from .env or .claude/.env (never hardcoded).
  - Build the OpenAI client.
  - On-disk response cache keyed by (template_version, model, prompt).
  - Cost meter with pre-call estimation and a hard --max-cost gate.
  - PID lockfile to prevent concurrent runs from corrupting state.
  - Optional schema redaction (T1, C1) for sensitive deployments.

The key never leaves this module; it is read once at startup and never
written to logs, cache files, or stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


# --- Pricing (USD per 1M tokens). Update if OpenAI revises rates. ---
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":         (0.150, 0.600),
    "gpt-4o-mini-2024-07-18": (0.150, 0.600),
    "gpt-4o":              (2.500, 10.000),
    "gpt-4o-2024-08-06":   (2.500, 10.000),
}


# --- Project paths ---
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "tools" / ".cache"
LOCK_DIR = CACHE_DIR / "locks"


# ---------- env ----------

_ENV_LOADED = False


def load_env() -> None:
    """Load OPENAI_API_KEY from .env or .claude/.env. Idempotent."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # python-dotenv is in main requirements.txt, so this should never fire.
        # If it does, fall back to whatever's already in the environment.
        _ENV_LOADED = True
        return

    candidates = [ROOT / ".env", ROOT / ".claude" / ".env"]
    for path in candidates:
        if path.is_file():
            # override=False: don't clobber an explicitly-set env var.
            load_dotenv(path, override=False)
    _ENV_LOADED = True


def get_api_key() -> str:
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.stderr.write(
            "ERROR: OPENAI_API_KEY not set.\n"
            "  Place 'OPENAI_API_KEY=sk-...' in either:\n"
            f"    {ROOT}/.env\n"
            f"    {ROOT}/.claude/.env\n"
            "  (Both are gitignored. Never commit a real key.)\n"
        )
        sys.exit(2)
    return key


def get_client():
    """Lazy import so the openai package isn't required for non-network code."""
    from openai import OpenAI

    return OpenAI(api_key=get_api_key())


# ---------- cost meter ----------


@dataclass
class CostMeter:
    """Tracks token usage and dollar cost across a tool run."""

    model: str
    max_cost_usd: float = 1.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def _rates(self) -> tuple[float, float]:
        if self.model not in PRICING:
            sys.stderr.write(
                f"WARN: unknown model '{self.model}', using gpt-4o-mini pricing as fallback.\n"
            )
            return PRICING["gpt-4o-mini"]
        return PRICING[self.model]

    def cost_usd(self) -> float:
        in_rate, out_rate = self._rates()
        return (
            self.prompt_tokens / 1_000_000 * in_rate
            + self.completion_tokens / 1_000_000 * out_rate
        )

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.calls += 1

    def estimate(self, prompt_tokens: int, est_completion_tokens: int) -> float:
        in_rate, out_rate = self._rates()
        return (
            prompt_tokens / 1_000_000 * in_rate
            + est_completion_tokens / 1_000_000 * out_rate
        )

    def assert_under_budget(self) -> None:
        """Refuse to keep going if we've crossed the cap mid-run."""
        if self.cost_usd() > self.max_cost_usd:
            sys.stderr.write(
                f"ERROR: cost cap hit. spent ${self.cost_usd():.4f} > "
                f"--max-cost ${self.max_cost_usd:.4f}\n"
            )
            sys.exit(3)

    def report(self) -> str:
        return (
            f"Calls: {self.calls} · "
            f"tokens in/out: {self.prompt_tokens:,}/{self.completion_tokens:,} · "
            f"cost: ${self.cost_usd():.4f}"
        )


# ---------- token estimate (no tiktoken dependency for cheap path) ----------


def rough_token_count(text: str) -> int:
    """Conservative ~chars/4 estimate. Used only for pre-call budget checks.

    Real counts come back via response.usage from the API.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ---------- response cache ----------


def _ensure_dirs() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_DIR.mkdir(parents=True, exist_ok=True)


def cache_key(template_version: int, model: str, prompt: str) -> str:
    payload = f"v{template_version}|{model}|{prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_get(key: str) -> dict[str, Any] | None:
    _ensure_dirs()
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # Corrupt cache file — wipe it.
        try:
            path.unlink()
        except OSError:
            pass
        return None


def cache_set(key: str, value: dict[str, Any]) -> None:
    _ensure_dirs()
    path = CACHE_DIR / f"{key}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(path)


# ---------- lockfile ----------


@contextmanager
def acquire_lock(name: str) -> Iterator[None]:
    """Prevent two copies of the same tool from running at once.

    Stale-PID-aware: if the locking process is gone, the lock is broken
    and we proceed.
    """
    _ensure_dirs()
    lock = LOCK_DIR / f"{name}.lock"

    if lock.is_file():
        try:
            existing_pid = int(lock.read_text().strip())
        except (ValueError, OSError):
            existing_pid = -1

        if existing_pid > 0 and _pid_alive(existing_pid):
            sys.stderr.write(
                f"ERROR: another {name} run is in progress (pid {existing_pid}).\n"
                f"  If you're sure it's dead, remove {lock}\n"
            )
            sys.exit(4)

    lock.write_text(str(os.getpid()))
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------- redaction ----------


@dataclass
class RedactionMap:
    """Two-way mapping between real and sanitized identifiers.

    Used so a sensitive schema can be annotated without leaking real
    table/column names to the LLM.
    """

    table: dict[str, str] = field(default_factory=dict)
    column: dict[str, str] = field(default_factory=dict)

    def redact_table(self, name: str) -> str:
        if name not in self.table:
            self.table[name] = f"T{len(self.table) + 1}"
        return self.table[name]

    def redact_column(self, name: str) -> str:
        if name not in self.column:
            self.column[name] = f"C{len(self.column) + 1}"
        return self.column[name]

    def unredact_table(self, code: str) -> str | None:
        for real, fake in self.table.items():
            if fake == code:
                return real
        return None

    def unredact_column(self, code: str) -> str | None:
        for real, fake in self.column.items():
            if fake == code:
                return real
        return None
