"""PHI-minimized orchestration decisions for Chief of Staff."""

from __future__ import annotations

import re
from dataclasses import dataclass


SAFE_RESULT = re.compile(r"^[A-Za-z0-9 _.,:;()\[\]/+&-]{0,500}$")
FINAL_PASS = "pass"
MAX_WRITER_LOOPS = 2


@dataclass(frozen=True)
class RouteDecision:
    assignee: str | None
    action: str
    terminal: bool = False


def initial_route(mode: str) -> RouteDecision:
    routes = {
        "copy": RouteDecision("copier", "copy_to_pending"),
        "write": RouteDecision("writer", "apply_corrections"),
        "correct": RouteDecision("verifier", "verify_case"),
        "verify": RouteDecision("verifier", "verify_case"),
    }
    try:
        return routes[mode]
    except KeyError as exc:
        raise ValueError("Unknown workflow mode") from exc


def after_verification(verdict: str, writer_loops: int) -> RouteDecision:
    verdict = verdict.strip().lower()
    if verdict == FINAL_PASS:
        return RouteDecision(None, "report_complete", terminal=True)
    if verdict not in {"fail", "mixed"}:
        return RouteDecision(None, "block_for_user", terminal=True)
    if writer_loops >= MAX_WRITER_LOOPS:
        return RouteDecision(None, "block_for_user", terminal=True)
    return RouteDecision("writer", "apply_corrections")


def after_write() -> RouteDecision:
    return RouteDecision("verifier", "verify_persistence")


def sanitize_summary(value: str) -> str:
    compact = " ".join(str(value).split())[:500]
    if not SAFE_RESULT.fullmatch(compact):
        return "Result withheld; inspect the active secure case."
    return compact
