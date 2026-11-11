"""Plan resolution for AI analysis.

Resolves explicit scope qualnames into IRScope objects.
"""

from dataclasses import dataclass, field
import logging
from typing import List, Optional

from pythonstan.ir import IRScope
from pythonstan.world import World

from pythonstan.compat.legacy_ai.config import Config

__all__ = ["AIPlan", "build_plan"]

logger = logging.getLogger(__name__)


@dataclass
class AIPlan:
    scopes: List[IRScope] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


def _unique(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def build_plan(config: Config) -> AIPlan:
    qualnames: List[str] = []
    if config.plan_scopes:
        qualnames.extend(config.plan_scopes)
    if config.entry_scope:
        qualnames.append(config.entry_scope)

    qualnames = _unique([q for q in qualnames if q])
    if not qualnames:
        return AIPlan()

    scope_manager = World().scope_manager
    plan = AIPlan()
    for qualname in qualnames:
        scope = scope_manager.get_module(qualname)
        if scope is None:
            plan.missing.append(qualname)
        else:
            plan.scopes.append(scope)

    if plan.missing:
        logger.warning("AI plan missing scopes: %s", ", ".join(plan.missing))

    return plan
