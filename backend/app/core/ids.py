"""Prefixed, sortable, self-describing identifiers (master plan §6.1)."""
from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _b32(n: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[n & 31])
        n >>= 5
    return "".join(reversed(out))


def new_id(prefix: str) -> str:
    """e.g. job_01HXQ8F3K2M7... — time-ordered so logs and demos read chronologically."""
    return f"{prefix}_{_b32(int(time.time() * 1000), 10)}{_b32(secrets.randbits(40), 8)}"


def session_id() -> str: return new_id("ses")
def scene_id() -> str: return new_id("scn")
def aoi_id() -> str: return new_id("aoi")
def job_id() -> str: return new_id("job")
def run_id() -> str: return new_id("run")
def artifact_id() -> str: return new_id("art")
def analysis_id() -> str: return new_id("ana")
def report_id() -> str: return new_id("rep")
def request_id() -> str: return new_id("req")
def chat_id() -> str: return new_id("cht")
