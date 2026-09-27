# stage_guard.py
"""Runs each analysis stage so that one failure cannot stop the report.

If a stage fails, it returns a safe default and adds a warning, and the other stages carry on.
The candidate still gets a report with whatever did work.
Stages handle the failures they expect themselves (silent audio, a model that cannot be reached), so only unexpected errors reach this guard.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


def run_stage(
    name: str,
    fn: Callable[[], Any],
    warnings: List[str],
    latency: Dict[str, float],
    default: Any,
) -> Any:
    """Run a stage and time it, returning `default` if it raises.

    `warnings` and `latency` are updated in place.
    The time is recorded even when the stage fails.
    KeyboardInterrupt and SystemExit are not caught.
    """
    t0 = time.perf_counter()
    try:
        out = fn()
    except Exception as exc:  # boundary guard by design
        logger.exception("Stage '%s' failed", name)
        warnings.append(f"Stage '{name}' failed: {exc}")
        out = default
    latency[name] = round(time.perf_counter() - t0, 3)
    return out
