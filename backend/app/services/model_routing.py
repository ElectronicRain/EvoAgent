from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentDefinition, ModelEndpoint


class OnlineModelRequired(RuntimeError):
    """Raised when an Agent cannot be routed to an enabled online chat endpoint."""


async def latest_chat_endpoint(
    db: AsyncSession,
    *,
    exclude_id: str | None = None,
) -> ModelEndpoint | None:
    statement = (
        select(ModelEndpoint)
        .where(
            ModelEndpoint.enabled.is_(True),
            ModelEndpoint.modality == "chat",
        )
        .order_by(desc(ModelEndpoint.updated_at), desc(ModelEndpoint.created_at))
    )
    if exclude_id:
        statement = statement.where(ModelEndpoint.id != exclude_id)
    return await db.scalar(statement)


def validate_chat_endpoint(endpoint: Any, *, label: str = "模型接口") -> None:
    if not endpoint:
        raise OnlineModelRequired(f"{label}不存在")
    if endpoint.modality != "chat":
        raise OnlineModelRequired(f"{label}不是对话模型接口")
    if not endpoint.enabled:
        raise OnlineModelRequired(f"{label}已停用")
    if not str(endpoint.base_url or "").strip():
        raise OnlineModelRequired(f"{label}缺少 Base URL")
    if not str(endpoint.default_model or "").strip():
        raise OnlineModelRequired(f"{label}缺少默认模型名称")


def bind_agent_to_endpoint(
    agent: AgentDefinition,
    endpoint: ModelEndpoint,
) -> bool:
    changed = any(
        (
            agent.model_endpoint_id != endpoint.id,
            agent.provider != endpoint.provider_type,
            agent.model != endpoint.default_model,
        )
    )
    agent.model_endpoint_id = endpoint.id
    agent.provider = endpoint.provider_type
    agent.model = endpoint.default_model
    return changed


async def resolve_agent_chat_endpoint(
    db: AsyncSession,
    agent: AgentDefinition,
    *,
    persist_binding: bool = True,
) -> ModelEndpoint | None:
    if not settings.require_online_agents and not agent.model_endpoint_id:
        return None
    endpoint = (
        await db.get(ModelEndpoint, agent.model_endpoint_id)
        if agent.model_endpoint_id
        else None
    )
    if endpoint and endpoint.enabled and endpoint.modality == "chat":
        if persist_binding:
            bind_agent_to_endpoint(agent, endpoint)
        return endpoint

    if not settings.require_online_agents:
        return None
    endpoint = await latest_chat_endpoint(db)
    if endpoint:
        if persist_binding:
            bind_agent_to_endpoint(agent, endpoint)
        return endpoint
    if settings.require_online_agents:
        raise OnlineModelRequired(
            f"Agent“{agent.name}”没有可用的在线对话模型接口。"
            "请先在“扩展与模型”中配置并启用接口。"
        )
    return None


async def migrate_agents_to_online_endpoint(
    db: AsyncSession,
    endpoint: ModelEndpoint | None = None,
) -> int:
    endpoint = endpoint or await latest_chat_endpoint(db)
    if not endpoint:
        return 0
    validate_chat_endpoint(endpoint)
    changed = 0
    agents = (await db.scalars(select(AgentDefinition))).all()
    for agent in agents:
        bound = (
            await db.get(ModelEndpoint, agent.model_endpoint_id)
            if agent.model_endpoint_id
            else None
        )
        if bound and bound.enabled and bound.modality == "chat":
            changed += int(bind_agent_to_endpoint(agent, bound))
            continue
        changed += int(bind_agent_to_endpoint(agent, endpoint))
    return changed
