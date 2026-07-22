from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApprovalPolicy
from .common import loads


@dataclass
class PolicyDecision:
    decision: str
    policy_id: str | None
    policy_name: str
    matched_rule: str
    reason: str


class ApprovalPolicyService:
    async def decide(
        self,
        db: AsyncSession,
        *,
        tool: str,
        risk: str,
        agent_id: str | None = None,
        policy_id: str | None = None,
    ) -> PolicyDecision:
        policy = await db.get(ApprovalPolicy, policy_id) if policy_id else None
        if not policy:
            policy = await db.scalar(
                select(ApprovalPolicy)
                .where(ApprovalPolicy.enabled.is_(True), ApprovalPolicy.is_default.is_(True))
                .order_by(ApprovalPolicy.priority)
            )
        if not policy:
            return PolicyDecision("ask", None, "系统兜底", "fallback", "未配置策略，默认询问")
        for index, rule in enumerate(loads(policy.rules_json, [])):
            conditions = rule.get("when", {})
            tools = conditions.get("tools") or ["*"]
            risks = conditions.get("risk_levels") or ["*"]
            agents = conditions.get("agent_ids") or ["*"]
            if tool not in tools and "*" not in tools:
                continue
            if risk not in risks and "*" not in risks:
                continue
            if agent_id and agent_id not in agents and "*" not in agents:
                continue
            return PolicyDecision(
                str(rule.get("decision", "ask")),
                policy.id,
                policy.name,
                str(rule.get("name") or f"rule-{index + 1}"),
                str(rule.get("reason") or "命中审批规则"),
            )
        return PolicyDecision("ask", policy.id, policy.name, "no-match", "没有匹配规则，默认询问")


approval_policy_service = ApprovalPolicyService()
