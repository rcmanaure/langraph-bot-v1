"""Policy-as-code: declarative per-tenant authorization evaluated at trust boundaries.

Usage:
    policy = TenantPolicy(tenant_id="acme", plan="basic")
    decision = engine.check(policy, "query", {"queries_this_month": 1500})
    if decision is Decision.DENY:
        raise HTTPException(429, "Query limit reached")
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import PLAN_LIMITS


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class TenantPolicy:
    """Declarative policy for a single tenant.

    Seeds defaults from PLAN_LIMITS; custom_rules override per-tenant.
    Add a field here when the policy surface grows — no engine changes needed.
    """
    tenant_id: str
    plan: str = "free"
    custom_rules: dict[str, Any] = field(default_factory=dict)

    def _limit(self, key: str) -> int:
        if key in self.custom_rules:
            return int(self.custom_rules[key])
        return PLAN_LIMITS.get(self.plan, PLAN_LIMITS["free"])[key]

    def max_docs(self) -> int:
        return self._limit("docs")

    def max_chunks(self) -> int:
        return self._limit("chunks")

    def max_queries_monthly(self) -> int:
        return self._limit("queries_monthly")


class PolicyEngine:
    """Evaluates a TenantPolicy against an action + context.

    Stateless — safe as a module-level singleton.
    Actions: "query" | "upload_doc" | "index_chunk"
    """

    def check(
        self,
        policy: TenantPolicy,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> Decision:
        ctx = context or {}

        if action == "query":
            if ctx.get("queries_this_month", 0) >= policy.max_queries_monthly():
                return Decision.DENY

        elif action == "upload_doc":
            if ctx.get("doc_count", 0) >= policy.max_docs():
                return Decision.DENY

        elif action == "index_chunk":
            if ctx.get("chunk_count", 0) >= policy.max_chunks():
                return Decision.DENY

        return Decision.ALLOW


# Module-level singleton — no state, safe to share across requests
engine = PolicyEngine()
