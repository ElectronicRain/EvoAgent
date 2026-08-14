from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db, session_scope
from .models import (
    AgentConversation,
    AgentDefinition,
    AgentArtifact,
    AgentGroup,
    AgentMessage,
    AgentRun,
    Approval,
    ApprovalPolicy,
    AuditLog,
    EvaluationCase,
    EvolutionProposal,
    Extension,
    KnowledgeBase,
    KnowledgeBaseGroup,
    KnowledgeBaseGroupMember,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    KnowledgeIngestionJob,
    KnowledgeProviderConfig,
    KnowledgeSource,
    LearningAssessment,
    LearningAttempt,
    LearningKnowledgeNode,
    LearningMemory,
    LearningMistake,
    LearningProject,
    LearningQuestion,
    LearningTask,
    LearningTutorTurn,
    ModelEndpoint,
    ResearchComment,
    ResearchArtifact,
    ResearchExperiment,
    ResearchIdea,
    ResearchLiterature,
    ResearchManuscript,
    ResearchManuscriptVersion,
    ResearchMemory,
    ResearchPresence,
    ResearchProject,
    ResearchProjectInvite,
    ResearchProjectLedger,
    ResearchProjectMember,
    ResearchProjectResource,
    ResearchReview,
    ResearchReviewItem,
    ResearchSourceReview,
    Skill,
    UserAccount,
    Workflow,
    WorkflowArtifact,
    WorkflowRun,
)
from .schemas import (
    AgentConversationCreate,
    AgentCreate,
    AgentGroupCreate,
    AgentGroupUpdate,
    AgentMessageCreate,
    AgentRAGEvaluationRequest,
    AgentRAGPreviewRequest,
    AgentRunRequest,
    AgentUpdate,
    ApprovalDecision,
    ApprovalPolicyCreate,
    ClassroomSpeechRequest,
    EvaluationCaseCreate,
    EvaluationCaseUpdate,
    EvolutionCreate,
    EvolutionDecision,
    EvolutionGoalAnalyze,
    EvolutionRollback,
    ExtensionCreate,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseGroupCreate,
    KnowledgeBaseGroupMembersUpdate,
    KnowledgeBaseGroupUpdate,
    KnowledgeProviderConfigUpdate,
    KnowledgeQueryRequest,
    KnowledgeSearchRequest,
    KnowledgeDocumentUpdate,
    KnowledgeSourceUpdate,
    LearningAssessmentGenerate,
    LearningAttemptCreate,
    LearningBindingsUpdate,
    LearningCompanionRequest,
    LearningDirectionRegenerate,
    LearningMemoryCreate,
    LearningMistakeUpdate,
    LearningPathReplan,
    LearningPlanGenerate,
    LearningProjectCreate,
    LearningProjectUpdate,
    LearningQuestionCreate,
    LearningTaskCreate,
    LearningTaskUpdate,
    LearningTutorChat,
    WebKnowledgeSourceCreate,
    DatabaseKnowledgeSourceCreate,
    APIKnowledgeSourceCreate,
    ModelEndpointCreate,
    ModelEndpointUpdate,
    ResearchVerificationComplete,
    ResearchSourceReviewCreate,
    ResearchCommentCreate,
    ResearchCommentUpdate,
    ResearchExperimentCreate,
    ResearchExperimentUpdate,
    ResearchFigureGenerate,
    ResearchFrontierTrack,
    ResearchIdeaChat,
    ResearchIdeaCreate,
    ResearchIdeaUpdate,
    ResearchLiteratureCreate,
    ResearchLiteratureSearch,
    ResearchManuscriptCreate,
    ResearchManuscriptAssist,
    ResearchManuscriptRestore,
    ResearchManuscriptUpdate,
    ResearchMemberCreate,
    ResearchInviteCreate,
    ResearchInviteJoin,
    ResearchMemoryCreate,
    ResearchPresenceUpdate,
    ResearchProjectCreate,
    ResearchProjectUpdate,
    ResearchReviewCreate,
    ResearchReviewItemUpdate,
    ResearchResourceCreate,
    ResearchSkillDraft,
    RuntimeSecurityConfigUpdate,
    TeachingPlanRequest,
    SkillCreate,
    TextDocumentCreate,
    ToolRunRequest,
    UserLogin,
    UserProfileUpdate,
    UserRegister,
    UserReplyStyleUpdate,
    WorkflowClarificationRequest,
    WorkflowCreate,
    WorkflowExpertChatRequest,
    WorkflowExpertMaterializeRequest,
    WorkflowRunControlRequest,
    WorkflowRunRequest,
)
from .services.agents import agent_engine
from .services.common import audit, dumps, loads
from .services.evolution import evolution_service
from .services.extensions import extension_service
from .services.knowledge import knowledge_service
from .services.learning_space import learning_space_service
from .services.advanced_academic import advanced_academic_service
from .services.knowledge_processing import extract_sections
from .services.knowledge_sources import knowledge_source_service
from .services.knowledge_vector import EmbeddingClient, RerankClient, get_knowledge_config
from .services.web_research import web_research_service
from .services.workflow_clarification import workflow_clarification_service
from .services.llm import (
    OpenAICompatibleImageProvider,
    OpenAICompatibleProvider,
    get_provider,
    provider_from_endpoint,
)
from .services.model_routing import (
    OnlineModelRequired,
    bind_agent_to_endpoint,
    latest_chat_endpoint,
    migrate_agents_to_online_endpoint,
    resolve_agent_chat_endpoint,
    validate_chat_endpoint,
)
from .services.secrets import secret_store
from .services.security import RuntimeSecurityContext, runtime_security_service
from .services.skill_security import SkillPackageError, skill_security_service
from .services.teaching import teaching_service
from .services.tools import tool_runtime
from .services.users import REPLY_STYLES, user_service
from .services.research_projects import research_project_service
from .services.document_exports import (
    content_disposition,
    markdown_to_docx,
    output_to_markdown,
    safe_docx_filename,
)
from .services.workflows import workflow_engine
from .services.workflow_expert import workflow_expert


router = APIRouter(prefix="/api")


async def append_research_ledger(
    db: AsyncSession,
    project_id: str,
    action: str,
    actor: str,
    resource_type: str = "research_project",
    resource_id: str = "",
    detail: dict[str, Any] | None = None,
) -> ResearchProjectLedger:
    previous = await db.scalar(
        select(ResearchProjectLedger)
        .where(ResearchProjectLedger.project_id == project_id)
        .order_by(desc(ResearchProjectLedger.sequence))
        .limit(1)
    )
    sequence = (previous.sequence if previous else 0) + 1
    previous_hash = previous.entry_hash if previous else "0" * 64
    detail_json = dumps(detail or {})
    canonical = json.dumps(
        {
            "project_id": project_id,
            "sequence": sequence,
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "detail": loads(detail_json, {}),
            "previous_hash": previous_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    entry = ResearchProjectLedger(
        project_id=project_id,
        sequence=sequence,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail_json=detail_json,
        previous_hash=previous_hash,
        entry_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    db.add(entry)
    await db.flush()
    return entry
active_conversation_tasks: set[asyncio.Task] = set()
active_workflow_tasks: set[asyncio.Task] = set()
active_evolution_tasks: set[asyncio.Task] = set()
active_knowledge_tasks: set[asyncio.Task] = set()


def row(model: Any) -> dict[str, Any]:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


async def require_user(db: AsyncSession, authorization: str | None) -> UserAccount:
    user = await user_service.resolve(db, authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def research_row(model: Any) -> dict[str, Any]:
    data = row(model)
    for key in (
        "settings_json",
        "tags_json",
        "metadata_json",
        "evidence_json",
        "scores_json",
        "design_json",
        "result_json",
        "roles_json",
        "cursor_json",
        "source_ids_json",
        "files_json",
        "report_json",
    ):
        if key in data:
            data[key.removesuffix("_json")] = loads(
                data[key],
                []
                if key in {"tags_json", "evidence_json", "roles_json", "source_ids_json"}
                else {},
            )
    return data


@router.get("/auth/status")
async def auth_status(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await user_service.resolve(db, authorization)
    count = await db.scalar(select(func.count(UserAccount.id))) or 0
    if user is None:
        return {
            "authenticated": False,
            "registration_required": count == 0,
            "user": None,
        }
    preference = await user_service.preference(db, user.id)
    return {
        "authenticated": True,
        "registration_required": False,
        "user": user_service.public_user(user, preference),
    }


@router.post("/auth/register", status_code=201)
async def register_user(
    payload: UserRegister, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        result = await user_service.register(
            db,
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await audit(
        db,
        "user.registered",
        "user_account",
        result["user"]["id"],
        {"claimed_legacy_data": result.get("claimed_legacy_data", False)},
        actor=result["user"]["username"],
    )
    return result


@router.post("/auth/login")
async def login_user(payload: UserLogin, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        result = await user_service.login(db, username=payload.username, password=payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    await audit(
        db,
        "user.logged_in",
        "user_account",
        result["user"]["id"],
        actor=result["user"]["username"],
    )
    return result


@router.post("/auth/logout", status_code=204)
async def logout_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await user_service.logout(db, authorization)
    return Response(status_code=204)


@router.get("/auth/me")
async def current_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    preference = await user_service.preference(db, user.id)
    return user_service.public_user(user, preference)


@router.patch("/users/me")
async def update_current_user(
    payload: UserProfileUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    preference = await user_service.preference(db, user.id)
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.avatar_color is not None:
        user.avatar_color = payload.avatar_color
    if payload.memory_enabled is not None:
        preference.memory_enabled = payload.memory_enabled
    await db.flush()
    await audit(
        db,
        "user.profile.updated",
        "user_account",
        user.id,
        actor=user.username,
    )
    return user_service.public_user(user, preference)


@router.get("/users/me/usage")
async def current_user_usage(
    range_name: str = Query(default="day", alias="range", pattern="^(day|week|month)$"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    return await user_service.usage(db, user.id, range_name)


@router.get("/users/me/profile")
async def current_user_profile(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    return await user_service.profile(db, user.id)


@router.get("/reply-styles")
async def reply_styles() -> list[dict[str, str]]:
    return REPLY_STYLES


@router.put("/users/me/reply-style")
async def update_reply_style(
    payload: UserReplyStyleUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    if payload.style_id == "custom" and not payload.custom_style.strip():
        raise HTTPException(status_code=422, detail="请填写自定义回复风格")
    preference = await user_service.preference(db, user.id)
    preference.reply_style_id = payload.style_id
    preference.custom_reply_style = payload.custom_style.strip()
    await db.flush()
    await audit(
        db,
        "user.reply_style.updated",
        "user_preference",
        user.id,
        {"style_id": payload.style_id},
        actor=user.username,
    )
    return {
        "reply_style_id": preference.reply_style_id,
        "custom_reply_style": preference.custom_reply_style,
    }


async def describe_agent_run_persistence(
    db: AsyncSession,
    *,
    conversation: AgentConversation,
    result: AgentRun,
    assistant_message: AgentMessage,
) -> dict[str, Any]:
    """Describe the business-database records that make a completed turn durable.

    Agent output is deliberately kept separate from knowledge bases. A knowledge
    base is changed only by an explicit import/archive action from the user.
    """

    artifacts = (
        await db.scalars(
            select(AgentArtifact)
            .where(
                AgentArtifact.run_id == result.id,
                AgentArtifact.conversation_id == conversation.id,
            )
            .order_by(AgentArtifact.created_at)
        )
    ).all()
    return {
        "type": "database_persisted",
        "run_id": result.id,
        "conversation_id": conversation.id,
        "message_id": assistant_message.id,
        "artifact_ids": [item.id for item in artifacts],
        "artifact_count": len(artifacts),
        "content_characters": len(assistant_message.content or ""),
        "storage": "business_database",
        "tables": ["agent_runs", "agent_messages"] + (["agent_artifacts"] if artifacts else []),
        "knowledge_base_updated": False,
    }


def endpoint_row(model: ModelEndpoint) -> dict[str, Any]:
    data = row(model)
    data.pop("api_key_ciphertext", None)
    data["has_api_key"] = bool(model.api_key_ciphertext)
    return data


def knowledge_config_row(model: KnowledgeProviderConfig) -> dict[str, Any]:
    data = row(model)
    data.pop("api_key_ciphertext", None)
    data["has_api_key"] = bool(model.api_key_ciphertext or settings.siliconflow_api_key)
    return data


def not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{name}不存在")


def mcp_response(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}


def mcp_error(payload: dict[str, Any], code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "error": {"code": code, "message": message},
    }


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    counts = {}
    for name, model in (
        ("agents", AgentDefinition),
        ("workflows", Workflow),
        ("knowledge_bases", KnowledgeBase),
        ("extensions", Extension),
        ("pending_approvals", Approval),
    ):
        statement = select(func.count(model.id))
        if model is Approval:
            statement = statement.where(Approval.status == "pending")
        counts[name] = int(await db.scalar(statement) or 0)
    recent_runs = (
        await db.scalars(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(8))
    ).all()
    security = await runtime_security_service.response(db)
    return {
        "counts": counts,
        "recent_runs": [row(item) for item in recent_runs],
        "runtime": {
            "database": "SQLite",
            "workspace": security["workspace_roots"][0],
            "safety": security["filesystem_mode"],
            "command_mode": security["command_mode"],
        },
    }


@router.get("/security/runtime")
async def get_runtime_security(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await runtime_security_service.response(db)


@router.put("/security/runtime")
async def update_runtime_security(
    payload: RuntimeSecurityConfigUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await runtime_security_service.update(
        db,
        filesystem_mode=payload.filesystem_mode,
        workspace_roots=payload.workspace_roots,
        command_mode=payload.command_mode,
        block_critical_commands=payload.block_critical_commands,
    )
    await audit(
        db,
        "runtime_security.updated",
        "runtime_security_config",
        item.id,
        {
            "filesystem_mode": item.filesystem_mode,
            "workspace_roots": payload.workspace_roots,
            "command_mode": item.command_mode,
            "block_critical_commands": item.block_critical_commands,
        },
    )
    return await runtime_security_service.response(db)


async def _agent_group_row(db: AsyncSession, group: AgentGroup) -> dict[str, Any]:
    agent_count = int(
        await db.scalar(
            select(func.count(AgentDefinition.id)).where(AgentDefinition.group_id == group.id)
        )
        or 0
    )
    return {**row(group), "agent_count": agent_count}


@router.get("/agent-groups")
async def list_agent_groups(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    groups = (
        await db.scalars(select(AgentGroup).order_by(AgentGroup.sort_order, AgentGroup.name))
    ).all()
    return [await _agent_group_row(db, group) for group in groups]


@router.post("/agent-groups", status_code=201)
async def create_agent_group(
    payload: AgentGroupCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    name = payload.name.strip()
    exists = await db.scalar(select(AgentGroup).where(func.lower(AgentGroup.name) == name.lower()))
    if exists:
        raise HTTPException(status_code=409, detail="Agent 分组名称已存在")
    group = AgentGroup(
        name=name,
        description=payload.description.strip(),
        color=payload.color,
        sort_order=payload.sort_order,
    )
    db.add(group)
    await db.flush()
    await audit(db, "agent.group_created", "agent_group", group.id, {"name": name})
    return await _agent_group_row(db, group)


@router.patch("/agent-groups/{group_id}")
async def update_agent_group(
    group_id: str,
    payload: AgentGroupUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    group = await db.get(AgentGroup, group_id)
    if not group:
        raise not_found("Agent 分组")
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = values["name"].strip()
        duplicate = await db.scalar(
            select(AgentGroup).where(
                AgentGroup.id != group_id,
                func.lower(AgentGroup.name) == values["name"].lower(),
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="Agent 分组名称已存在")
    if "description" in values:
        values["description"] = values["description"].strip()
    for key, value in values.items():
        setattr(group, key, value)
    await audit(db, "agent.group_updated", "agent_group", group.id)
    return await _agent_group_row(db, group)


@router.delete("/agent-groups/{group_id}", status_code=204)
async def delete_agent_group(group_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    group = await db.get(AgentGroup, group_id)
    if not group:
        raise not_found("Agent 分组")
    await db.execute(
        update(AgentDefinition).where(AgentDefinition.group_id == group_id).values(group_id=None)
    )
    await db.delete(group)
    await audit(db, "agent.group_deleted", "agent_group", group_id)
    return Response(status_code=204)


@router.get("/agents")
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    items = (await db.scalars(select(AgentDefinition).order_by(AgentDefinition.name))).all()
    return [row(item) for item in items]


async def verified_agent_skill_ids(db: AsyncSession, skill_ids: list[str]) -> list[str]:
    requested = list(dict.fromkeys(skill_ids))
    if not requested:
        return []
    verified = list(
        (
            await db.scalars(
                select(Skill).where(
                    Skill.id.in_(requested),
                    Skill.enabled.is_(True),
                    Skill.validation_status == "verified",
                )
            )
        ).all()
    )
    verified_ids = {item.id for item in verified}
    invalid = [item for item in requested if item not in verified_ids]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Agent 只能绑定已启用且通过安全校验的 Skill",
                "invalid_skill_ids": invalid,
            },
        )
    return requested


@router.post("/agents", status_code=201)
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if await db.scalar(select(AgentDefinition).where(AgentDefinition.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Agent slug 已存在")
    endpoint_id = payload.model_endpoint_id or None
    endpoint = (
        await db.get(ModelEndpoint, endpoint_id)
        if endpoint_id
        else (await latest_chat_endpoint(db) if settings.require_online_agents else None)
    )
    if endpoint_id:
        try:
            validate_chat_endpoint(endpoint, label="回答模型接口")
        except OnlineModelRequired as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if settings.require_online_agents and not endpoint:
        raise HTTPException(
            status_code=422,
            detail="必须先配置并启用一个在线对话模型接口，Agent 不允许使用离线演示模型",
        )
    endpoint_id = endpoint.id if endpoint else None
    image_endpoint_id = payload.image_model_endpoint_id or None
    if image_endpoint_id:
        image_endpoint = await db.get(ModelEndpoint, image_endpoint_id)
        if not image_endpoint:
            raise not_found("图片模型接口")
        if image_endpoint.modality != "image":
            raise HTTPException(status_code=422, detail="图片模型必须选择图片生成接口")
    group_id = payload.group_id or None
    if group_id and not await db.get(AgentGroup, group_id):
        raise not_found("Agent 分组")
    skill_ids = await verified_agent_skill_ids(db, payload.skills)
    item = AgentDefinition(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        system_prompt=payload.system_prompt,
        provider=endpoint.provider_type if endpoint else payload.provider,
        model_endpoint_id=endpoint_id,
        image_model_endpoint_id=image_endpoint_id,
        group_id=group_id,
        model=endpoint.default_model if endpoint else payload.model,
        temperature=payload.temperature,
        tools_json=dumps(list(dict.fromkeys([*payload.tools, "exec"]))),
        skills_json=dumps(skill_ids),
        knowledge_bases_json=dumps(payload.knowledge_bases),
        rag_config_json=dumps(payload.rag_config.model_dump()),
        generation_config_json=dumps(payload.generation_config.model_dump()),
        permissions_json=dumps(payload.permissions),
        is_template=payload.is_template,
    )
    db.add(item)
    await db.flush()
    await audit(db, "agent.created", "agent", item.id)
    return row(item)


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(AgentDefinition, agent_id)
    if not item:
        raise not_found("Agent")
    return row(item)


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: str, payload: AgentUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(AgentDefinition, agent_id)
    if not item:
        raise not_found("Agent")
    values = payload.model_dump(exclude_unset=True)
    if "skills" in values:
        values["skills"] = await verified_agent_skill_ids(db, values["skills"])
    json_fields = {
        "tools": "tools_json",
        "skills": "skills_json",
        "knowledge_bases": "knowledge_bases_json",
        "rag_config": "rag_config_json",
        "generation_config": "generation_config_json",
        "permissions": "permissions_json",
    }
    for key, value in values.items():
        if key in json_fields:
            if key == "tools":
                value = list(dict.fromkeys([*value, "exec"]))
            json_value = value.model_dump() if hasattr(value, "model_dump") else value
            setattr(item, json_fields[key], dumps(json_value))
        elif key in {"model_endpoint_id", "image_model_endpoint_id"}:
            endpoint_id = value or None
            endpoint = await db.get(ModelEndpoint, endpoint_id) if endpoint_id else None
            if endpoint_id and not endpoint:
                raise not_found("模型接口")
            expected_modality = "image" if key == "image_model_endpoint_id" else "chat"
            if endpoint and endpoint.modality != expected_modality:
                label = "图片生成" if expected_modality == "image" else "对话"
                raise HTTPException(
                    status_code=422,
                    detail=f"该字段必须选择{label}模型接口",
                )
            if key == "model_endpoint_id" and endpoint and not endpoint.enabled:
                raise HTTPException(status_code=422, detail="选择的对话模型接口已停用")
            setattr(item, key, endpoint_id)
        elif key == "group_id":
            group_id = value or None
            if group_id and not await db.get(AgentGroup, group_id):
                raise not_found("Agent 分组")
            item.group_id = group_id
        else:
            setattr(item, key, value)
    try:
        endpoint = await resolve_agent_chat_endpoint(db, item)
    except OnlineModelRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if endpoint:
        bind_agent_to_endpoint(item, endpoint)
    await audit(db, "agent.updated", "agent", item.id)
    return row(item)


@router.post("/agents/{agent_id}/rag/preview")
async def preview_agent_rag(
    agent_id: str,
    payload: AgentRAGPreviewRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise not_found("Agent")
    return await agent_engine.preview_rag(
        db,
        agent,
        payload.query,
        conversation_messages=payload.history,
    )


@router.post("/agents/{agent_id}/rag/evaluate")
async def evaluate_agent_rag(
    agent_id: str,
    payload: AgentRAGEvaluationRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await db.get(AgentDefinition, agent_id)
    if not agent:
        raise not_found("Agent")
    statement = select(EvaluationCase).where(EvaluationCase.enabled.is_(True))
    if payload.case_ids:
        statement = statement.where(EvaluationCase.id.in_(payload.case_ids))
    cases = (
        await db.scalars(statement.order_by(EvaluationCase.created_at).limit(payload.limit))
    ).all()
    results: list[dict[str, Any]] = []
    weighted_recall = 0.0
    weighted_mrr = 0.0
    weighted_ndcg = 0.0
    total_weight = 0.0
    for case in cases:
        started = time.perf_counter()
        preview = await agent_engine.preview_rag(db, agent, case.input_text)
        chunks = preview.get("chunks", [])
        expected = loads(case.expected_keywords_json, [])
        context = str(preview.get("context") or "").lower()
        matched = [keyword for keyword in expected if keyword.lower() in context]
        recall = len(matched) / max(1, len(expected)) if expected else float(bool(chunks))
        relevant_ranks = [
            index
            for index, chunk in enumerate(chunks, 1)
            if not expected
            or any(
                keyword.lower() in str(chunk.get("context") or "").lower() for keyword in expected
            )
        ]
        reciprocal_rank = 1 / relevant_ranks[0] if relevant_ranks else 0.0
        dcg = sum(1 / math.log2(rank + 1) for rank in relevant_ranks)
        ideal_count = min(len(relevant_ranks), len(chunks))
        ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
        weight = float(case.weight or 1)
        total_weight += weight
        weighted_recall += recall * weight
        weighted_mrr += reciprocal_rank * weight
        weighted_ndcg += ndcg * weight
        results.append(
            {
                "case_id": case.id,
                "name": case.name,
                "category": case.category,
                "recall_at_k": round(recall, 4),
                "reciprocal_rank": round(reciprocal_rank, 4),
                "ndcg": round(ndcg, 4),
                "matched_keywords": matched,
                "expected_keywords": expected,
                "citations": len(preview.get("citations", [])),
                "list_items": sum(
                    int(item.get("item_count") or 0)
                    for item in preview.get("trace", {}).get("list_contexts", [])
                ),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )
    denominator = total_weight or 1.0
    summary = {
        "cases": len(results),
        "recall_at_k": round(weighted_recall / denominator, 4),
        "mrr": round(weighted_mrr / denominator, 4),
        "ndcg": round(weighted_ndcg / denominator, 4),
        "average_latency_ms": round(
            sum(item["latency_ms"] for item in results) / max(1, len(results))
        ),
    }
    return {"agent_id": agent.id, "summary": summary, "results": results}


@router.post("/agents/{agent_id}/run")
async def run_agent(
    agent_id: str,
    payload: AgentRunRequest,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    user = await user_service.resolve(db, authorization)
    context = dict(payload.context)
    if user is not None:
        preference = await user_service.preference(db, user.id)
        context.update(
            {
                "user_id": user.id,
                "reply_style_prompt": user_service.reply_style_prompt(preference),
            }
        )
    result = await agent_engine.run(db, agent_id, payload.input, context)
    return row(result)


@router.get("/agents/{agent_id}/conversations")
async def list_agent_conversations(
    agent_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    user = await user_service.resolve(db, authorization)
    query = select(AgentConversation).where(AgentConversation.agent_id == agent_id)
    if user is not None:
        query = query.where(AgentConversation.user_id == user.id)
    items = (await db.scalars(query.order_by(desc(AgentConversation.updated_at)))).all()
    result = []
    for item in items:
        data = row(item)
        latest = await db.scalar(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == item.id)
            .order_by(desc(AgentMessage.created_at))
            .limit(1)
        )
        if latest and latest.run_id:
            run = await db.get(AgentRun, latest.run_id)
            data["run_status"] = run.status if run else None
            data["run_id"] = latest.run_id
        else:
            data["run_status"] = None
            data["run_id"] = None
        result.append(data)
    return result


@router.post("/agents/{agent_id}/conversations", status_code=201)
async def create_agent_conversation(
    agent_id: str,
    payload: AgentConversationCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    user = await user_service.resolve(db, authorization)
    item = AgentConversation(
        agent_id=agent_id,
        user_id=user.id if user else None,
        title=payload.title,
    )
    db.add(item)
    await db.flush()
    await audit(db, "conversation.created", "agent_conversation", item.id)
    return row(item)


@router.get("/conversations/{conversation_id}/messages")
async def list_conversation_messages(
    conversation_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if not await db.get(AgentConversation, conversation_id):
        raise not_found("会话")
    items = (
        await db.scalars(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(AgentMessage.created_at)
        )
    ).all()
    run_ids = {item.run_id for item in items if item.run_id}
    runs = (
        {
            item.id: item
            for item in (await db.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))).all()
        }
        if run_ids
        else {}
    )
    result = []
    for item in items:
        data = row(item)
        run = runs.get(item.run_id)
        data["run_status"] = run.status if run else None
        data["run_trace_json"] = run.trace_json if run else None
        data["run_error"] = run.error if run else None
        result.append(data)
    return result


@router.get("/conversations/{conversation_id}/artifacts")
async def list_conversation_artifacts(
    conversation_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if not await db.get(AgentConversation, conversation_id):
        raise not_found("会话")
    items = (
        await db.scalars(
            select(AgentArtifact)
            .where(AgentArtifact.conversation_id == conversation_id)
            .order_by(desc(AgentArtifact.created_at))
        )
    ).all()
    result = []
    for item in items:
        data = row(item)
        data["storage"] = "business_database"
        data["content_characters"] = len(item.content or "")
        data["format"] = item.kind.upper()
        result.append(data)
    return result


@router.post("/conversations/{conversation_id}/teaching-plan")
async def create_teaching_plan(
    conversation_id: str,
    payload: TeachingPlanRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    conversation = await db.get(AgentConversation, conversation_id)
    if not conversation:
        raise not_found("会话")
    artifact = await db.get(AgentArtifact, payload.artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise not_found("文档")
    agent = await db.get(AgentDefinition, conversation.agent_id)
    if not agent:
        raise not_found("Agent")
    bound_endpoint = (
        await db.get(ModelEndpoint, agent.model_endpoint_id) if agent.model_endpoint_id else None
    )
    endpoint = bound_endpoint
    if not endpoint:
        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "chat",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
    provider = provider_from_endpoint(endpoint) if endpoint else get_provider(agent.provider)
    result = await teaching_service.create_plan(
        artifact.content,
        agent,
        provider,
        payload.section_indices,
        endpoint.default_model if endpoint else agent.model,
    )
    result["model_endpoint"] = endpoint.name if endpoint else None
    result["cloud_tts_available"] = bool(endpoint and "siliconflow.cn" in endpoint.base_url.lower())
    await audit(
        db,
        "classroom.plan.generated",
        "agent_artifact",
        artifact.id,
        {"agent_id": agent.id, "mode": result["mode"], "sections": len(result["sections"])},
    )
    return result


@router.post("/conversations/{conversation_id}/classroom-speech")
async def create_classroom_speech(
    conversation_id: str,
    payload: ClassroomSpeechRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    conversation = await db.get(AgentConversation, conversation_id)
    if not conversation:
        raise not_found("会话")
    agent = await db.get(AgentDefinition, conversation.agent_id)
    endpoint = (
        await db.get(ModelEndpoint, agent.model_endpoint_id)
        if agent and agent.model_endpoint_id
        else None
    )
    if not endpoint:
        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "chat",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
    if not endpoint or "siliconflow.cn" not in endpoint.base_url.lower():
        raise HTTPException(status_code=400, detail="当前模型接口不支持内置云端真人语音")
    styles = {
        "natural": "请用自然亲切、富有耐心的真人教师语气讲解，语速舒缓，有恰当停顿。",
        "lively": "请用生动活泼、富有感染力的真人教师语气讲解，重点处适度加强语气。",
        "rigorous": "请用沉稳严谨、清晰可信的真人教师语气讲解，公式处放慢并准确断句。",
    }
    base_url = endpoint.base_url.rstrip("/")
    speech_url = (
        f"{base_url}/audio/speech" if base_url.endswith("/v1") else f"{base_url}/v1/audio/speech"
    )
    model = "FunAudioLLM/CosyVoice2-0.5B"
    request_body = {
        "model": model,
        "voice": f"{model}:{payload.voice}",
        "input": f"{styles[payload.style]}<|endofprompt|>{payload.input}",
        "response_format": "mp3",
        "sample_rate": 44100,
        "speed": 1.0,
    }
    headers = loads(endpoint.headers_json, {})
    headers["Authorization"] = f"Bearer {secret_store.decrypt(endpoint.api_key_ciphertext)}"
    async with httpx.AsyncClient(timeout=endpoint.timeout_seconds) as client:
        response = await client.post(speech_url, json=request_body, headers=headers)
    if response.is_error:
        detail = response.text.replace("\n", " ")[:500]
        raise HTTPException(
            status_code=502,
            detail=f"云端语音生成失败 HTTP {response.status_code}: {detail}",
        )
    await audit(
        db,
        "classroom.speech.generated",
        "agent_conversation",
        conversation_id,
        {"voice": payload.voice, "style": payload.style, "characters": len(payload.input)},
    )
    return Response(content=response.content, media_type="audio/mpeg")


@router.get("/conversations/{conversation_id}/source-reviews")
async def list_source_reviews(
    conversation_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if not await db.get(AgentConversation, conversation_id):
        raise not_found("会话")
    items = (
        await db.scalars(
            select(ResearchSourceReview)
            .where(ResearchSourceReview.conversation_id == conversation_id)
            .order_by(desc(ResearchSourceReview.updated_at))
        )
    ).all()
    return [row(item) for item in items]


@router.post("/conversations/{conversation_id}/source-reviews")
async def review_research_source(
    conversation_id: str,
    payload: ResearchSourceReviewCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(AgentConversation, conversation_id):
        raise not_found("会话")
    item = await db.scalar(
        select(ResearchSourceReview).where(
            ResearchSourceReview.conversation_id == conversation_id,
            ResearchSourceReview.url == payload.url,
        )
    )
    if not item:
        item = ResearchSourceReview(
            conversation_id=conversation_id,
            run_id=payload.run_id,
            url=payload.url,
        )
        db.add(item)
    item.run_id = payload.run_id
    item.title = payload.title
    item.decision = payload.decision
    item.credibility_json = dumps(payload.credibility)
    await db.flush()
    await audit(
        db,
        "research_source.reviewed",
        "research_source",
        item.id,
        {"url": payload.url, "decision": payload.decision},
    )
    return row(item)


@router.get("/learning-subject-packs/computer-science")
async def get_computer_learning_subject_pack(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await require_user(db, authorization)
    return await learning_space_service.subject_pack(db)


@router.get("/learning-projects")
async def list_learning_projects(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    projects = (
        await db.scalars(
            select(LearningProject)
            .where(LearningProject.owner_id == user.id)
            .order_by(desc(LearningProject.updated_at))
        )
    ).all()
    return [await learning_space_service.project_payload(db, item) for item in projects]


@router.post("/learning-projects", status_code=201)
async def create_learning_project(
    payload: LearningProjectCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    pack = await learning_space_service.subject_pack(db)
    agent_bindings, workflow_bindings = learning_space_service.default_bindings(pack)
    data = payload.model_dump()
    track = data.pop("track")
    item = LearningProject(
        owner_id=user.id,
        **data,
        knowledge_group_id=pack["group"]["id"],
        knowledge_base_ids_json=dumps([base["id"] for base in pack["knowledge_bases"]]),
        agent_bindings_json=dumps(agent_bindings),
        workflow_bindings_json=dumps(workflow_bindings),
    )
    db.add(item)
    await db.flush()
    await learning_space_service.scaffold(db, item, track)
    await audit(db, "learning_project.created", "learning_project", item.id, {"track": track}, actor=user.username)
    return await learning_space_service.project_payload(db, item)


@router.get("/learning-projects/{project_id}")
async def get_learning_project(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    return await learning_space_service.project_payload(db, project)


@router.patch("/learning-projects/{project_id}")
async def update_learning_project(
    project_id: str,
    payload: LearningProjectUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    updates = payload.model_dump(exclude_unset=True)
    settings_update = updates.pop("settings", None)
    path_inputs_changed = bool({"name", "project_type", "description", "target", "current_level", "target_level", "weekly_hours", "deadline"} & set(updates))
    for key, value in updates.items():
        setattr(project, key, value)
    settings = {**loads(project.settings_json, {}), **(settings_update or {})}
    if path_inputs_changed:
        settings["direction_profile_stale"] = True
    project.settings_json = dumps(settings)
    await audit(db, "learning_project.updated", "learning_project", project.id, {"fields": list(payload.model_fields_set)}, actor=user.username)
    return await learning_space_service.project_payload(db, project)


@router.delete("/learning-projects/{project_id}", status_code=204)
async def delete_learning_project(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    await db.delete(project)
    await audit(db, "learning_project.deleted", "learning_project", project_id, actor=user.username)
    return Response(status_code=204)


@router.get("/learning-projects/{project_id}/workspace")
async def get_learning_workspace(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    return await learning_space_service.workspace(db, project)


@router.get("/learning-projects/{project_id}/diagnostic")
async def get_learning_diagnostic(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    return await advanced_academic_service.learning_diagnostic(db, project)


@router.get("/learning-projects/{project_id}/personalized-path")
async def get_personalized_learning_path(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    return await advanced_academic_service.learning_path(db, project)


@router.post("/learning-projects/{project_id}/personalized-path/replan")
async def replan_personalized_learning_path(
    project_id: str,
    payload: LearningPathReplan,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    tasks = await learning_space_service.generate_plan(
        db,
        project,
        regenerate=payload.regenerate_plan,
        start_at=payload.start_at,
        focus=payload.focus,
    )
    path = await advanced_academic_service.learning_path(db, project)
    await audit(
        db,
        "learning_path.replanned",
        "learning_project",
        project.id,
        {"tasks": len(tasks), "focus": payload.focus, "diagnostic": path["diagnostic"]["overall_score"]},
        actor=user.username,
    )
    return {
        "path": path,
        "tasks": [learning_space_service.model_row(item) for item in tasks],
    }


@router.get("/learning-projects/{project_id}/personal-space")
async def get_learning_personal_space(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    settings_data = loads(project.settings_json, {})
    memories = (
        await db.scalars(
            select(LearningMemory)
            .where(LearningMemory.project_id == project.id)
            .order_by(desc(LearningMemory.updated_at))
        )
    ).all()
    diagnostic = await advanced_academic_service.learning_diagnostic(db, project)
    return {
        "project": await learning_space_service.project_payload(db, project),
        "direction_profile": settings_data.get("direction_profile", {}),
        "preferences": settings_data.get("learning_preferences", {
            "explanation_depth": "step_by_step",
            "mentor_style": "socratic",
            "session_minutes": 45,
            "resource_format": "mixed",
        }),
        "diagnostic": diagnostic,
        "memory_summary": {
            "total": len(memories),
            "locked": sum(item.locked for item in memories),
            "categories": dict(Counter(item.category for item in memories)),
            "recent": [row(item) for item in memories[:6]],
        },
    }


@router.post("/learning-projects/{project_id}/companion/session")
async def create_learning_companion_session(
    project_id: str,
    payload: LearningCompanionRequest,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    result = await advanced_academic_service.companion_session(
        db, project, payload.minutes, payload.mood, payload.goal
    )
    await audit(
        db,
        "learning_companion.session_created",
        "learning_project",
        project.id,
        {"minutes": payload.minutes, "mood": payload.mood},
        actor=user.username,
    )
    return result


@router.post("/learning-projects/{project_id}/direction/regenerate")
async def regenerate_learning_direction(
    project_id: str,
    payload: LearningDirectionRegenerate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    result = await learning_space_service.rebuild_direction(
        db, project, track=payload.track, keep_memories=payload.keep_memories
    )
    await audit(
        db,
        "learning_direction.regenerated",
        "learning_project",
        project.id,
        {
            "track": payload.track or loads(project.settings_json, {}).get("track"),
            "keep_memories": payload.keep_memories,
            "direction_signature": loads(project.settings_json, {}).get("direction_profile", {}).get("signature"),
        },
        actor=user.username,
    )
    return result


@router.put("/learning-projects/{project_id}/bindings")
async def update_learning_bindings(
    project_id: str,
    payload: LearningBindingsUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    for agent_id in payload.agents.values():
        if agent_id and not await db.get(AgentDefinition, agent_id):
            raise HTTPException(status_code=422, detail=f"Agent 不存在：{agent_id}")
    for workflow_id in payload.workflows.values():
        if workflow_id and not await db.get(Workflow, workflow_id):
            raise HTTPException(status_code=422, detail=f"工作流不存在：{workflow_id}")
    project.agent_bindings_json = dumps({**loads(project.agent_bindings_json, {}), **payload.agents})
    project.workflow_bindings_json = dumps({**loads(project.workflow_bindings_json, {}), **payload.workflows})
    if payload.knowledge_base_ids is not None:
        known = set((await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(payload.knowledge_base_ids)))).all())
        if known != set(payload.knowledge_base_ids):
            raise HTTPException(status_code=422, detail="绑定中包含不存在的知识库")
        project.knowledge_base_ids_json = dumps(payload.knowledge_base_ids)
    if payload.knowledge_group_id is not None:
        if payload.knowledge_group_id and not await db.get(KnowledgeBaseGroup, payload.knowledge_group_id):
            raise HTTPException(status_code=422, detail="知识库分组不存在")
        project.knowledge_group_id = payload.knowledge_group_id or None
    await audit(db, "learning_project.bindings_updated", "learning_project", project.id, actor=user.username)
    return await learning_space_service.project_payload(db, project)


@router.post("/learning-projects/{project_id}/plan/generate")
async def generate_learning_plan(
    project_id: str,
    payload: LearningPlanGenerate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    items = await learning_space_service.generate_plan(db, project, regenerate=payload.regenerate, start_at=payload.start_at, focus=payload.focus)
    await audit(db, "learning_plan.generated", "learning_project", project.id, {"tasks": len(items)}, actor=user.username)
    return [learning_space_service.model_row(item) if hasattr(learning_space_service, "model_row") else row(item) for item in items]


@router.post("/learning-projects/{project_id}/tasks", status_code=201)
async def create_learning_task(
    project_id: str,
    payload: LearningTaskCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    if payload.knowledge_node_id:
        node = await db.get(LearningKnowledgeNode, payload.knowledge_node_id)
        if not node or node.project_id != project.id:
            raise HTTPException(status_code=422, detail="知识节点不属于当前学习方向")
    item = LearningTask(project_id=project.id, source="user", **payload.model_dump())
    db.add(item)
    await db.flush()
    return row(item)


@router.patch("/learning-projects/{project_id}/tasks/{task_id}")
async def update_learning_task(
    project_id: str,
    task_id: str,
    payload: LearningTaskUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await learning_space_service.access(db, project_id, user)
    item = await db.get(LearningTask, task_id)
    if not item or item.project_id != project_id:
        raise not_found("学习任务")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value)
    if updates.get("status") == "completed" and "progress" not in updates:
        item.progress = 100
    await audit(db, "learning_task.updated", "learning_task", item.id, {"status": item.status}, actor=user.username)
    return row(item)


@router.post("/learning-projects/{project_id}/tutor")
async def chat_learning_tutor(
    project_id: str,
    payload: LearningTutorChat,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    if payload.knowledge_node_id:
        node = await db.get(LearningKnowledgeNode, payload.knowledge_node_id)
        if not node or node.project_id != project.id:
            raise HTTPException(status_code=422, detail="知识节点不属于当前学习方向")
    item = await learning_space_service.tutor(db, project, user, **payload.model_dump())
    await audit(db, "learning_tutor.replied", "learning_project", project.id, {"mode": payload.mode, "citations": len(loads(item.citations_json, []))}, actor=user.username)
    return learning_space_service.model_row(item) if hasattr(learning_space_service, "model_row") else research_row(item)


@router.post("/learning-projects/{project_id}/questions", status_code=201)
async def create_learning_question(
    project_id: str,
    payload: LearningQuestionCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    if payload.knowledge_node_id:
        node = await db.get(LearningKnowledgeNode, payload.knowledge_node_id)
        if not node or node.project_id != project.id:
            raise HTTPException(status_code=422, detail="知识节点不属于当前学习方向")
    data = payload.model_dump()
    item = LearningQuestion(
        project_id=project.id,
        knowledge_node_id=data.pop("knowledge_node_id"),
        question_type=data.pop("question_type"),
        prompt=data.pop("prompt"),
        difficulty=data.pop("difficulty"),
        options_json=dumps(data.pop("options")),
        answer_json=dumps(data.pop("answer")),
        rubric_json=dumps(data.pop("rubric")),
        source_refs_json=dumps(data.pop("source_refs")),
        generated_by_agent_id=loads(project.agent_bindings_json, {}).get("practice") or None,
    )
    db.add(item)
    await db.flush()
    return learning_space_service.model_row(item) if hasattr(learning_space_service, "model_row") else row(item)


@router.post("/learning-projects/{project_id}/attempts", status_code=201)
async def submit_learning_attempt(
    project_id: str,
    payload: LearningAttemptCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    question = await db.get(LearningQuestion, payload.question_id)
    if not question or question.project_id != project.id:
        raise not_found("练习题")
    attempt, mistake = await learning_space_service.submit_attempt(db, project, question, payload.answer, payload.agent_id or loads(project.agent_bindings_json, {}).get("review"))
    await audit(db, "learning_attempt.graded", "learning_attempt", attempt.id, {"score": attempt.score, "correct": attempt.is_correct}, actor=user.username)
    return {"attempt": learning_space_service.model_row(attempt) if hasattr(learning_space_service, "model_row") else row(attempt), "mistake": row(mistake) if mistake else None, "question": learning_space_service.model_row(question) if hasattr(learning_space_service, "model_row") else row(question)}


@router.patch("/learning-projects/{project_id}/mistakes/{mistake_id}")
async def update_learning_mistake(
    project_id: str,
    mistake_id: str,
    payload: LearningMistakeUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await learning_space_service.access(db, project_id, user)
    item = await db.get(LearningMistake, mistake_id)
    if not item or item.project_id != project_id:
        raise not_found("错题记录")
    if payload.correction is not None:
        item.correction = payload.correction
    if payload.status is not None:
        item.status = payload.status
    if payload.reviewed:
        item.review_count += 1
        delays = [1, 3, 7, 14, 30]
        item.next_review_at = datetime.now(timezone.utc) + timedelta(days=delays[min(item.review_count, len(delays)) - 1])
        if item.review_count >= 3 and item.status == "reviewing":
            item.status = "mastered"
    await audit(db, "learning_mistake.updated", "learning_mistake", item.id, {"status": item.status, "reviews": item.review_count}, actor=user.username)
    return row(item)


@router.post("/learning-projects/{project_id}/memories", status_code=201)
async def create_learning_memory(
    project_id: str,
    payload: LearningMemoryCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await learning_space_service.access(db, project_id, user)
    item = LearningMemory(project_id=project_id, **payload.model_dump())
    db.add(item)
    await db.flush()
    return row(item)


@router.delete("/learning-projects/{project_id}/memories/{memory_id}", status_code=204)
async def delete_learning_memory(
    project_id: str,
    memory_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await learning_space_service.access(db, project_id, user)
    item = await db.get(LearningMemory, memory_id)
    if not item or item.project_id != project_id:
        raise not_found("学习记忆")
    await db.delete(item)
    return Response(status_code=204)


@router.post("/learning-projects/{project_id}/assessments", status_code=201)
async def generate_learning_assessment(
    project_id: str,
    payload: LearningAssessmentGenerate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    item = await learning_space_service.assess(db, project, payload.period)
    await audit(db, "learning_assessment.generated", "learning_assessment", item.id, {"score": item.overall_score}, actor=user.username)
    return learning_space_service.model_row(item) if hasattr(learning_space_service, "model_row") else row(item)


@router.post("/learning-projects/{project_id}/workflow/run")
async def run_learning_workflow(
    project_id: str,
    payload: WorkflowRunRequest,
    module: str = Query(default="learning_loop"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project = await learning_space_service.access(db, project_id, user)
    workflow_id = loads(project.workflow_bindings_json, {}).get(module)
    if not workflow_id:
        raise HTTPException(status_code=422, detail="当前模块未绑定工作流")
    try:
        item = await workflow_engine.run(db, workflow_id, {**payload.input, "learning_project_id": project.id, "learning_target": project.target})
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(db, "learning_workflow.started", "workflow_run", item.id, {"module": module}, actor=user.username)
    return row(item)


@router.get("/research-projects")
async def list_research_projects(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    member_project_ids = select(ResearchProjectMember.project_id).where(
        ResearchProjectMember.user_id == user.id,
        ResearchProjectMember.status == "active",
    )
    projects = (
        await db.scalars(
            select(ResearchProject)
            .where(
                (ResearchProject.owner_id == user.id) | (ResearchProject.id.in_(member_project_ids))
            )
            .order_by(desc(ResearchProject.updated_at))
        )
    ).all()
    return [await research_project_service.project_payload(db, item, user) for item in projects]


@router.post("/research-projects", status_code=201)
async def create_research_project(
    payload: ResearchProjectCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    item = ResearchProject(owner_id=user.id, **payload.model_dump())
    db.add(item)
    await db.flush()
    await audit(db, "research.project.created", "research_project", item.id, actor=user.username)
    await append_research_ledger(db, item.id, "project.created", user.username, resource_id=item.id, detail={"name": item.name})
    return await research_project_service.project_payload(db, item, user)


@router.get("/research-projects/{project_id}")
async def get_research_project(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user)
    data = await research_project_service.project_payload(db, project, user)
    data["context"] = await research_project_service.context(db, project)
    return data


@router.patch("/research-projects/{project_id}")
async def update_research_project(
    project_id: str,
    payload: ResearchProjectUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    values = payload.model_dump(exclude_unset=True)
    minimum_role = "editor" if set(values) == {"settings"} else "manager"
    project, _ = await research_project_service.access(db, project_id, user, minimum_role)
    if "settings" in values:
        project.settings_json = dumps(values.pop("settings") or {})
    for key, value in values.items():
        setattr(project, key, value)
    await audit(db, "research.project.updated", "research_project", project.id, actor=user.username)
    return await research_project_service.project_payload(db, project, user)


@router.get("/research-projects/{project_id}/members")
async def list_research_members(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user)
    owner = await db.get(UserAccount, project.owner_id)
    result = [
        {
            "id": f"owner:{project.owner_id}",
            "user_id": project.owner_id,
            "username": owner.username,
            "display_name": owner.display_name,
            "role": "owner",
            "status": "active",
        }
    ]
    members = (
        await db.scalars(
            select(ResearchProjectMember).where(ResearchProjectMember.project_id == project_id)
        )
    ).all()
    for member in members:
        account = await db.get(UserAccount, member.user_id)
        result.append(
            {
                **row(member),
                "username": account.username if account else "",
                "display_name": account.display_name if account else "已删除用户",
            }
        )
    return result


@router.post("/research-projects/{project_id}/members", status_code=201)
async def add_research_member(
    project_id: str,
    payload: ResearchMemberCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "manager")
    account = await db.scalar(select(UserAccount).where(UserAccount.username == payload.username))
    if not account:
        raise HTTPException(status_code=404, detail="协作者必须先在本机注册账户")
    if account.id == project.owner_id:
        raise HTTPException(status_code=409, detail="项目负责人已经拥有最高权限")
    member = await db.scalar(
        select(ResearchProjectMember).where(
            ResearchProjectMember.project_id == project_id,
            ResearchProjectMember.user_id == account.id,
        )
    )
    if not member:
        member = ResearchProjectMember(project_id=project_id, user_id=account.id)
        db.add(member)
    member.role, member.status = payload.role, "active"
    await db.flush()
    await audit(
        db,
        "research.member.added",
        "research_project",
        project_id,
        {"member": account.username, "role": payload.role},
        actor=user.username,
    )
    await append_research_ledger(db, project_id, "member.added", user.username, "member", account.id, {"username": account.username, "role": payload.role})
    return {**row(member), "username": account.username, "display_name": account.display_name}


@router.post("/research-projects/{project_id}/invites", status_code=201)
async def create_research_invite(
    project_id: str,
    payload: ResearchInviteCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "manager")
    code = f"EVO-{secrets.token_urlsafe(18)}"
    item = ResearchProjectInvite(
        project_id=project_id,
        created_by=user.id,
        code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        code_hint=code[-6:],
        role=payload.role,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=payload.expires_hours),
        max_uses=payload.max_uses,
    )
    db.add(item)
    await db.flush()
    await append_research_ledger(db, project_id, "invite.created", user.username, "invite", item.id, {"role": item.role, "expires_at": item.expires_at.isoformat(), "max_uses": item.max_uses})
    return {**row(item), "code": code}


@router.post("/research-projects/join", status_code=201)
async def join_research_project(
    payload: ResearchInviteJoin,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    code_hash = hashlib.sha256(payload.code.strip().encode("utf-8")).hexdigest()
    item = await db.scalar(select(ResearchProjectInvite).where(ResearchProjectInvite.code_hash == code_hash))
    now = datetime.now(timezone.utc)
    expires_at = item.expires_at.replace(tzinfo=timezone.utc) if item and item.expires_at.tzinfo is None else (item.expires_at if item else now)
    if not item or item.status != "active" or expires_at <= now or item.use_count >= item.max_uses:
        raise HTTPException(status_code=422, detail="邀请码无效、已过期或使用次数已耗尽")
    project = await db.get(ResearchProject, item.project_id)
    if not project:
        raise not_found("科研项目")
    if user.id != project.owner_id:
        member = await db.scalar(select(ResearchProjectMember).where(ResearchProjectMember.project_id == item.project_id, ResearchProjectMember.user_id == user.id))
        if not member:
            member = ResearchProjectMember(project_id=item.project_id, user_id=user.id)
            db.add(member)
        member.role, member.status = item.role, "active"
    item.use_count += 1
    if item.use_count >= item.max_uses:
        item.status = "consumed"
    await db.flush()
    await append_research_ledger(db, item.project_id, "member.joined_by_invite", user.username, "member", user.id, {"role": item.role, "invite_hint": item.code_hint})
    return await research_project_service.project_payload(db, project, user)


@router.get("/research-projects/{project_id}/ledger")
async def list_research_ledger(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (await db.scalars(select(ResearchProjectLedger).where(ResearchProjectLedger.project_id == project_id).order_by(ResearchProjectLedger.sequence))).all()
    valid = True
    previous_hash = "0" * 64
    result = []
    for item in items:
        canonical = json.dumps({"project_id": item.project_id, "sequence": item.sequence, "actor": item.actor, "action": item.action, "resource_type": item.resource_type, "resource_id": item.resource_id, "detail": loads(item.detail_json, {}), "previous_hash": previous_hash}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        valid = valid and item.previous_hash == previous_hash and hashlib.sha256(canonical.encode("utf-8")).hexdigest() == item.entry_hash
        previous_hash = item.entry_hash
        result.append({**row(item), "detail": loads(item.detail_json, {})})
    return {"verified": valid, "head_hash": previous_hash if items else "", "entries": list(reversed(result))}


@router.delete("/research-projects/{project_id}/members/{member_id}", status_code=204)
async def remove_research_member(
    project_id: str,
    member_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "manager")
    member = await db.get(ResearchProjectMember, member_id)
    if not member or member.project_id != project_id:
        raise not_found("项目成员")
    await db.delete(member)
    await audit(db, "research.member.removed", "research_project", project_id, actor=user.username)


@router.get("/research-projects/{project_id}/resources")
async def list_research_resources(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchProjectResource)
            .where(ResearchProjectResource.project_id == project_id)
            .order_by(desc(ResearchProjectResource.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/resources", status_code=201)
async def add_research_resource(
    project_id: str,
    payload: ResearchResourceCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.scalar(
        select(ResearchProjectResource).where(
            ResearchProjectResource.project_id == project_id,
            ResearchProjectResource.resource_type == payload.resource_type,
            ResearchProjectResource.resource_id == payload.resource_id,
        )
    )
    if not item:
        item = ResearchProjectResource(
            project_id=project_id,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
        )
        db.add(item)
    item.label = payload.label
    item.metadata_json = dumps(payload.metadata)
    await db.flush()
    return research_row(item)


@router.get("/research-projects/{project_id}/literature")
async def list_project_literature(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchLiterature)
            .where(ResearchLiterature.project_id == project_id)
            .order_by(desc(ResearchLiterature.credibility), desc(ResearchLiterature.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/literature", status_code=201)
async def create_project_literature(
    project_id: str,
    payload: ResearchLiteratureCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    values = payload.model_dump(exclude={"tags"})
    item = ResearchLiterature(
        project_id=project_id, created_by=user.id, tags_json=dumps(payload.tags), **values
    )
    db.add(item)
    await db.flush()
    return research_row(item)


@router.post("/research-projects/{project_id}/literature/search")
async def search_project_literature(
    project_id: str,
    payload: ResearchLiteratureSearch,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    items = await research_project_service.search_literature(
        db, project, user, payload.query, payload.target_count, payload.year_from, payload.year_to
    )
    await audit(
        db,
        "research.literature.searched",
        "research_project",
        project_id,
        {"query": payload.query, "count": len(items)},
        actor=user.username,
    )
    return [research_row(item) for item in items]


@router.patch("/research-projects/{project_id}/literature/{literature_id}")
async def update_project_literature(
    project_id: str,
    literature_id: str,
    payload: ResearchLiteratureCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchLiterature, literature_id)
    if not item or item.project_id != project_id:
        raise not_found("文献")
    for key, value in payload.model_dump(exclude={"tags"}).items():
        setattr(item, key, value)
    item.tags_json = dumps(payload.tags)
    return research_row(item)


@router.post("/research-projects/{project_id}/literature/synthesize")
async def synthesize_project_literature(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    items = (
        await db.scalars(
            select(ResearchLiterature).where(
                ResearchLiterature.project_id == project_id,
                ResearchLiterature.status.in_(["included", "priority"]),
            )
        )
    ).all()
    if not items:
        raise HTTPException(status_code=422, detail="请先纳入至少一篇文献")
    evidence = "\n".join(
        f"[{index}] {item.title}; {item.authors}; {item.year}; DOI:{item.doi or '无'}; 摘要:{item.abstract[:1600]}"
        for index, item in enumerate(items, 1)
    )
    content = await research_project_service.chat(
        db,
        system="你是系统综述助手。只能依据给定文献，正文使用[文献 N]，区分事实、推论和研究空白。",
        user_message=f"研究问题：{project.research_question or project.description}\n\n文献：\n{evidence}\n\n生成综述、主题脉络、方法脉络、争议、研究空白和参考文献。",
        max_output_tokens=10000,
    )
    return {"content": content, "sources": len(items), "traceable": True}


@router.post("/research-projects/{project_id}/literature/figure", status_code=201)
async def create_research_figure(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    figure = await research_project_service.academic_figure(db, project)
    item = ResearchArtifact(
        project_id=project_id,
        created_by=user.id,
        kind="academic-graph",
        title=figure["title"],
        content=dumps(figure),
        source_ids_json=dumps(figure["source_ids"]),
        metadata_json=dumps({"style": "academic", "traceable": True}),
    )
    db.add(item)
    await db.flush()
    return {**research_row(item), "figure": figure}


@router.get("/research-projects/{project_id}/frontier")
async def list_research_frontier_snapshots(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    return await advanced_academic_service.list_artifacts(db, project_id, ["frontier-snapshot"])


@router.post("/research-projects/{project_id}/frontier/track", status_code=201)
async def track_research_frontier(
    project_id: str,
    payload: ResearchFrontierTrack,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    query = payload.query.strip() or project.research_question or project.description or project.name
    refresh_error = ""
    if payload.refresh:
        current_year = datetime.now(timezone.utc).year
        try:
            await research_project_service.search_literature(
                db,
                project,
                user,
                query,
                payload.target_count,
                current_year - payload.recent_years + 1,
                current_year,
            )
        except Exception as exc:  # 保留本地题录降级能力，错误在结果中明确呈现。
            refresh_error = str(exc)
    result = await advanced_academic_service.frontier_snapshot(
        db, project, user, query, payload.recent_years
    )
    result["refresh"] = {
        "requested": payload.refresh,
        "succeeded": not refresh_error,
        "error": refresh_error,
    }
    await audit(
        db,
        "research.frontier.tracked",
        "research_project",
        project.id,
        {"query": query, "sources": len(result["sources"]), "refresh_error": refresh_error},
        actor=user.username,
    )
    return result


@router.get("/research-projects/{project_id}/data-assets")
async def list_research_data_assets(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = await advanced_academic_service.list_artifacts(
        db, project_id, ["research-dataset", "publication-figure"]
    )
    for item in items:
        payload_data = item.get("payload", {})
        records = payload_data.pop("records", None)
        if records is not None:
            payload_data["sample"] = records[:20]
            payload_data["stored_record_count"] = len(records)
    return items


@router.post("/research-projects/{project_id}/data-assets/upload", status_code=201)
async def upload_research_dataset(
    project_id: str,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    filename = PurePosixPath((file.filename or "dataset.csv").replace("\\", "/")).name
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix not in {"csv", "tsv", "json"}:
        raise HTTPException(status_code=422, detail="科研数据仅支持 CSV、TSV 和 JSON")
    data = await file.read(8_000_001)
    result = await advanced_academic_service.store_dataset(db, project, user, filename, data)
    await audit(
        db,
        "research.dataset.uploaded",
        "research_project",
        project.id,
        {"filename": filename, "rows": result["profile"]["rows"], "fields": len(result["fields"])},
        actor=user.username,
    )
    return result


@router.post("/research-projects/{project_id}/figures", status_code=201)
async def generate_research_publication_figure(
    project_id: str,
    payload: ResearchFigureGenerate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    dataset = await db.get(ResearchArtifact, payload.dataset_id)
    if not dataset or dataset.project_id != project_id or dataset.kind != "research-dataset":
        raise not_found("科研数据集")
    result = await advanced_academic_service.create_publication_figure(
        db, project, user, dataset, payload.model_dump()
    )
    await audit(
        db,
        "research.figure.generated",
        "research_project",
        project.id,
        {"dataset_id": dataset.id, "chart_type": result["spec"]["chart_type"]},
        actor=user.username,
    )
    return result


@router.get("/research-projects/{project_id}/figures/{artifact_id}/svg")
async def get_research_publication_figure_svg(
    project_id: str,
    artifact_id: str,
    download: bool = Query(default=False),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.get(ResearchArtifact, artifact_id)
    if not item or item.project_id != project_id or item.kind != "publication-figure":
        raise not_found("论文图表")
    payload_data = loads(item.content, {})
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="figure-{item.id}.svg"'
    return Response(content=payload_data.get("svg", ""), media_type="image/svg+xml", headers=headers)


@router.delete("/research-projects/{project_id}/data-assets/{artifact_id}", status_code=204)
async def delete_research_data_asset(
    project_id: str,
    artifact_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchArtifact, artifact_id)
    if not item or item.project_id != project_id or item.kind not in {"research-dataset", "publication-figure"}:
        raise not_found("科研数据资产")
    await db.delete(item)
    await audit(db, "research.data_asset.deleted", "research_project", project_id, {"kind": item.kind}, actor=user.username)
    return Response(status_code=204)


@router.get("/research-projects/{project_id}/ideas")
async def list_research_ideas(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchIdea)
            .where(ResearchIdea.project_id == project_id)
            .order_by(desc(ResearchIdea.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/ideas", status_code=201)
async def create_research_idea(
    project_id: str,
    payload: ResearchIdeaCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    values = payload.model_dump(exclude={"evidence", "scores"})
    item = ResearchIdea(
        project_id=project_id,
        created_by=user.id,
        evidence_json=dumps(payload.evidence),
        scores_json=dumps(payload.scores),
        **values,
    )
    db.add(item)
    await db.flush()
    return research_row(item)


@router.patch("/research-projects/{project_id}/ideas/{idea_id}")
async def update_research_idea(
    project_id: str,
    idea_id: str,
    payload: ResearchIdeaUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchIdea, idea_id)
    if not item or item.project_id != project_id:
        raise not_found("Idea")
    values = payload.model_dump(exclude_unset=True, exclude={"evidence", "scores"})
    for key, value in values.items():
        setattr(item, key, value)
    if payload.evidence is not None:
        item.evidence_json = dumps(payload.evidence)
    if payload.scores is not None:
        item.scores_json = dumps(payload.scores)
    return research_row(item)


@router.delete("/research-projects/{project_id}/ideas/{idea_id}", status_code=204)
async def delete_research_idea(
    project_id: str,
    idea_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchIdea, idea_id)
    if not item or item.project_id != project_id:
        raise not_found("Idea")
    linked = await db.scalar(
        select(func.count(ResearchExperiment.id)).where(ResearchExperiment.idea_id == idea_id)
    ) or 0
    if linked:
        raise HTTPException(status_code=409, detail="该 Idea 已承接实验，请保留其研究溯源或先解除实验关联")
    await db.delete(item)
    await append_research_ledger(
        db, project_id, "idea.deleted", user.username, "research_idea", idea_id,
        {"title": item.title},
    )
    return Response(status_code=204)


@router.post("/research-projects/{project_id}/ideas/{idea_id}/experiment", status_code=201)
async def convert_idea_to_experiment(
    project_id: str,
    idea_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    idea = await db.get(ResearchIdea, idea_id)
    if not idea or idea.project_id != project_id:
        raise not_found("Idea")
    idea.status = "adopted"
    item = ResearchExperiment(
        project_id=project_id,
        idea_id=idea.id,
        created_by=user.id,
        title=f"验证：{idea.title}",
        objective=idea.problem,
        hypothesis=idea.hypothesis,
        design_json=dumps(
            {
                "method": idea.method,
                "variables": {"independent": "待确认", "dependent": "待确认", "controls": []},
                "dataset": "待确认",
                "baselines": [],
                "metrics": [],
                "ablations": [],
                "random_seed": 42,
                "repetitions": 3,
                "statistical_test": "待确认",
                "failure_criteria": "待确认",
            }
        ),
    )
    db.add(item)
    await db.flush()
    return research_row(item)


@router.post("/research-projects/{project_id}/ideas/chat")
async def chat_research_idea(
    project_id: str,
    payload: ResearchIdeaChat,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    if payload.agent_id:
        agent = await db.get(AgentDefinition, payload.agent_id)
        if not agent or agent.status not in {"active", "candidate"}:
            raise not_found("Idea 专家 Agent")
        context = await research_project_service.context(db, project)
        run = await agent_engine.run(
            db,
            agent.id,
            (
                "你正在科研项目的 Idea 苏格拉底对话模块工作。先回应用户，再只追问一个最关键的问题；"
                "检查新颖性、可证伪性、数据可得性、方法匹配和反例，不得虚构结论。\n\n"
                f"项目上下文：\n{context[:30000]}\n\n用户：{payload.message}"
            ),
            {"user_id": user.id},
            conversation_messages=payload.history,
        )
        answer = run.output_text
    else:
        answer = await research_project_service.explore_idea(
            db, project, payload.message, payload.history
        )
    return {"answer": answer}


@router.get("/research-projects/{project_id}/memories")
async def list_research_memories(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchMemory)
            .where(ResearchMemory.project_id == project_id)
            .order_by(desc(ResearchMemory.locked), desc(ResearchMemory.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/memories", status_code=201)
async def create_research_memory(
    project_id: str,
    payload: ResearchMemoryCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = ResearchMemory(project_id=project_id, created_by=user.id, **payload.model_dump())
    db.add(item)
    await db.flush()
    return research_row(item)


@router.delete("/research-projects/{project_id}/memories/{memory_id}", status_code=204)
async def delete_research_memory(
    project_id: str,
    memory_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchMemory, memory_id)
    if not item or item.project_id != project_id:
        raise not_found("科研记忆")
    if item.locked and (await research_project_service.access(db, project_id, user))[1] not in {
        "owner",
        "manager",
    }:
        raise HTTPException(status_code=403, detail="锁定记忆只能由项目负责人或管理员删除")
    await db.delete(item)


@router.post("/research-projects/{project_id}/skills", status_code=201)
async def create_project_skill(
    project_id: str,
    payload: ResearchSkillDraft,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "manager")
    memories = (
        (
            await db.scalars(
                select(ResearchMemory).where(
                    ResearchMemory.project_id == project_id,
                    ResearchMemory.id.in_(payload.memory_ids),
                )
            )
        ).all()
        if payload.memory_ids
        else []
    )
    instructions = (
        "# 项目科研 Skill\n\n"
        + (payload.description or "依据项目已确认记忆执行任务。")
        + "\n\n## 已确认规则\n"
        + "\n".join(f"- [{item.category}] {item.content}" for item in memories)
    )
    skill = Skill(
        name=payload.name,
        description=payload.description,
        instructions=instructions,
        enabled=False,
        validation_status="pending",
        risk_level="unknown",
    )
    db.add(skill)
    await db.flush()
    db.add(
        ResearchProjectResource(
            project_id=project_id,
            resource_type="skill",
            resource_id=skill.id,
            label=skill.name,
            metadata_json=dumps({"status": "pending_validation"}),
        )
    )
    return research_row(skill)


@router.get("/research-projects/{project_id}/experiments")
async def list_research_experiments(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchExperiment)
            .where(ResearchExperiment.project_id == project_id)
            .order_by(desc(ResearchExperiment.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/experiments", status_code=201)
async def create_research_experiment(
    project_id: str,
    payload: ResearchExperimentCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    values = payload.model_dump(exclude={"design", "result"})
    item = ResearchExperiment(
        project_id=project_id,
        created_by=user.id,
        design_json=dumps(payload.design),
        result_json=dumps(payload.result),
        **values,
    )
    db.add(item)
    await db.flush()
    return research_row(item)


@router.patch("/research-projects/{project_id}/experiments/{experiment_id}")
async def update_research_experiment(
    project_id: str,
    experiment_id: str,
    payload: ResearchExperimentUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchExperiment, experiment_id)
    if not item or item.project_id != project_id:
        raise not_found("实验")
    for key, value in payload.model_dump(exclude_unset=True, exclude={"design", "result"}).items():
        setattr(item, key, value)
    if payload.design is not None:
        item.design_json = dumps(payload.design)
    if payload.result is not None:
        item.result_json = dumps(payload.result)
    await audit(
        db,
        "research.experiment.updated",
        "research_experiment",
        item.id,
        {"status": item.status},
        actor=user.username,
    )
    return research_row(item)


@router.get("/research-projects/{project_id}/manuscripts")
async def list_research_manuscripts(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchManuscript)
            .where(ResearchManuscript.project_id == project_id)
            .order_by(desc(ResearchManuscript.updated_at))
        )
    ).all()
    return [research_row(item) for item in items]


@router.post("/research-projects/{project_id}/manuscripts", status_code=201)
async def create_research_manuscript(
    project_id: str,
    payload: ResearchManuscriptCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    content = payload.content or (
        "\\documentclass[UTF8]{ctexart}\n\\usepackage{amsmath,graphicx,booktabs}\n\\title{"
        + payload.title
        + "}\n\\author{"
        + user.display_name
        + "}\n\\begin{document}\n\\maketitle\n\\begin{abstract}\n请填写摘要。\n\\end{abstract}\n\\section{引言}\n请填写研究背景、问题与贡献。\n\\section{相关工作}\n\\section{方法}\n\\section{实验}\n\\section{结论}\n\\bibliographystyle{gbt7714-numerical}\n\\bibliography{references}\n\\end{document}\n"
    )
    item = ResearchManuscript(
        project_id=project_id,
        created_by=user.id,
        title=payload.title,
        content=content,
        bibliography=payload.bibliography,
        main_file="main.tex",
    )
    initial_files = {"main.tex": research_project_service.file_record("main.tex", content.encode("utf-8"))}
    if payload.bibliography:
        initial_files["references.bib"] = research_project_service.file_record(
            "references.bib", payload.bibliography.encode("utf-8")
        )
    item.files_json = dumps(initial_files)
    db.add(item)
    await db.flush()
    db.add(
        ResearchManuscriptVersion(
            manuscript_id=item.id,
            author_id=user.id,
            version=1,
            content=item.content,
            bibliography=item.bibliography,
            main_file=item.main_file,
            files_json=item.files_json,
            change_summary="创建论文",
        )
    )
    return research_row(item)


@router.post("/research-projects/{project_id}/manuscripts/import", status_code=201)
async def import_research_manuscript(
    project_id: str,
    files: list[UploadFile] = File(...),
    title: str = Form(default=""),
    main_file: str = Form(default=""),
    paths_json: str = Form(default="[]"),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    requested_paths = loads(paths_json, [])
    if requested_paths and len(requested_paths) != len(files):
        raise HTTPException(status_code=422, detail="上传文件与相对路径数量不一致")
    uploads: list[tuple[str, bytes]] = []
    for index, upload in enumerate(files):
        raw_path = requested_paths[index] if requested_paths else (upload.filename or f"file-{index}")
        uploads.append((str(raw_path), await upload.read()))
    project_files, detected_main = research_project_service.import_latex_uploads(
        uploads, main_file
    )
    main_content = project_files[detected_main]["content"]
    detected_title = re.search(r"\\title\{([^}]*)\}", main_content)
    manuscript_title = (title.strip() or (detected_title.group(1).strip() if detected_title else "") or PurePosixPath(detected_main).stem)[:240]
    bibliography = next(
        (record["content"] for path, record in project_files.items() if path.lower().endswith(".bib") and record["encoding"] == "utf8"),
        "",
    )
    item = ResearchManuscript(
        project_id=project_id,
        created_by=user.id,
        title=manuscript_title,
        content=main_content,
        bibliography=bibliography,
        main_file=detected_main,
        files_json=dumps(project_files),
    )
    db.add(item)
    await db.flush()
    db.add(
        ResearchManuscriptVersion(
            manuscript_id=item.id,
            author_id=user.id,
            version=1,
            content=item.content,
            bibliography=item.bibliography,
            main_file=item.main_file,
            files_json=item.files_json,
            change_summary=f"导入 LaTeX 项目（{len(project_files)} 个文件）",
        )
    )
    await audit(
        db,
        "research.manuscript.imported",
        "research_manuscript",
        item.id,
        {"main_file": detected_main, "file_count": len(project_files)},
        actor=user.username,
    )
    await append_research_ledger(db, project_id, "manuscript.imported", user.username, "research_manuscript", item.id, {"main_file": detected_main, "file_count": len(project_files), "version": 1})
    data = research_row(item)
    data["files"] = project_files
    data["preview"] = research_project_service.latex_preview(
        research_project_service.flatten_latex(project_files, detected_main)
    )
    return data


@router.get("/research-projects/{project_id}/manuscripts/{manuscript_id}")
async def get_research_manuscript(
    project_id: str,
    manuscript_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    data = research_row(item)
    files = research_project_service.manuscript_files(item)
    data["files"] = files
    data["preview"] = research_project_service.latex_preview(
        research_project_service.flatten_latex(files, item.main_file)
    )
    return data


@router.post("/research-projects/{project_id}/manuscripts/{manuscript_id}/export")
async def export_research_manuscript(
    project_id: str,
    manuscript_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    content = research_project_service.export_latex_zip(item)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="latex-project-v{item.version}.zip"'},
    )


@router.get("/research-projects/{project_id}/manuscripts/{manuscript_id}/versions/{version}/diff")
async def diff_research_manuscript_version(
    project_id: str,
    manuscript_id: str,
    version: int,
    file_path: str = "main.tex",
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    manuscript = await db.get(ResearchManuscript, manuscript_id)
    if not manuscript or manuscript.project_id != project_id:
        raise not_found("论文")
    source = await db.scalar(
        select(ResearchManuscriptVersion).where(
            ResearchManuscriptVersion.manuscript_id == manuscript_id,
            ResearchManuscriptVersion.version == version,
        )
    )
    if not source:
        raise not_found("论文历史版本")
    safe_path = research_project_service.safe_project_path(file_path)
    return {
        "version": version,
        "current_version": manuscript.version,
        "file_path": safe_path,
        "diff": research_project_service.version_diff(source, manuscript, safe_path),
    }


@router.put("/research-projects/{project_id}/manuscripts/{manuscript_id}")
async def update_research_manuscript(
    project_id: str,
    manuscript_id: str,
    payload: ResearchManuscriptUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    if item.version != payload.base_version:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "论文已被其他协作者更新，请合并后重试",
                "current_version": item.version,
                "current_content": item.content,
            },
        )
    files = research_project_service.manuscript_files(item)
    if payload.files is not None:
        files = research_project_service.normalize_files(payload.files)
    else:
        files[item.main_file] = research_project_service.file_record(
            item.main_file, payload.content.encode("utf-8")
        )
        if payload.bibliography:
            files["references.bib"] = research_project_service.file_record(
                "references.bib", payload.bibliography.encode("utf-8")
            )
    main_file = research_project_service.detect_main_file(files, payload.main_file)
    main_record = files[main_file]
    if main_record.get("encoding") != "utf8":
        raise HTTPException(status_code=422, detail="主文档必须是 UTF-8 文本 .tex 文件")
    bibliography = next(
        (record.get("content", "") for path, record in files.items() if path.lower().endswith(".bib") and record.get("encoding") == "utf8"),
        payload.bibliography,
    )
    item.version += 1
    item.main_file = main_file
    item.files_json = dumps(files)
    item.content = main_record["content"]
    item.bibliography = bibliography
    db.add(
        ResearchManuscriptVersion(
            manuscript_id=item.id,
            author_id=user.id,
            version=item.version,
            content=item.content,
            bibliography=item.bibliography,
            main_file=item.main_file,
            files_json=item.files_json,
            change_summary=payload.change_summary or f"保存版本 {item.version}",
        )
    )
    await db.flush()
    await audit(
        db,
        "research.manuscript.saved",
        "research_manuscript",
        item.id,
        {"version": item.version},
        actor=user.username,
    )
    await append_research_ledger(db, project_id, "manuscript.version_saved", user.username, "research_manuscript", item.id, {"version": item.version, "summary": payload.change_summary})
    return research_row(item)


@router.get("/research-projects/{project_id}/manuscripts/{manuscript_id}/versions")
async def list_manuscript_versions(
    project_id: str,
    manuscript_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    versions = (
        await db.scalars(
            select(ResearchManuscriptVersion)
            .where(ResearchManuscriptVersion.manuscript_id == manuscript_id)
            .order_by(desc(ResearchManuscriptVersion.version))
        )
    ).all()
    result = []
    for version in versions:
        account = await db.get(UserAccount, version.author_id) if version.author_id else None
        result.append(
            {**row(version), "author_name": account.display_name if account else "未知成员"}
        )
    return result


@router.post("/research-projects/{project_id}/manuscripts/{manuscript_id}/restore")
async def restore_manuscript_version(
    project_id: str,
    manuscript_id: str,
    payload: ResearchManuscriptRestore,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    manuscript = await db.get(ResearchManuscript, manuscript_id)
    if not manuscript or manuscript.project_id != project_id:
        raise not_found("论文")
    if manuscript.version != payload.base_version:
        raise HTTPException(status_code=409, detail="论文已被其他成员更新，请重新加载")
    source = await db.scalar(
        select(ResearchManuscriptVersion).where(
            ResearchManuscriptVersion.manuscript_id == manuscript_id,
            ResearchManuscriptVersion.version == payload.version,
        )
    )
    if not source:
        raise not_found("论文历史版本")
    manuscript.version += 1
    manuscript.content = source.content
    manuscript.bibliography = source.bibliography
    manuscript.main_file = source.main_file
    manuscript.files_json = source.files_json
    db.add(
        ResearchManuscriptVersion(
            manuscript_id=manuscript.id,
            author_id=user.id,
            version=manuscript.version,
            content=manuscript.content,
            bibliography=manuscript.bibliography,
            main_file=manuscript.main_file,
            files_json=manuscript.files_json,
            change_summary=f"恢复自 v{source.version}",
        )
    )
    await db.flush()
    return research_row(manuscript)


@router.post("/research-projects/{project_id}/manuscripts/{manuscript_id}/assist")
async def assist_research_manuscript(
    project_id: str,
    manuscript_id: str,
    payload: ResearchManuscriptAssist,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "editor")
    manuscript = await db.get(ResearchManuscript, manuscript_id)
    if not manuscript or manuscript.project_id != project_id:
        raise not_found("论文")
    if payload.agent_id:
        agent = await db.get(AgentDefinition, payload.agent_id)
        if not agent or agent.status not in {"active", "candidate"}:
            raise not_found("写作专家 Agent")
        target = payload.selection.strip() or manuscript.content[:120000]
        run = await agent_engine.run(
            db,
            agent.id,
            (
                f"你正在科研项目的 LaTeX 写作模块执行 {payload.task}。保持 LaTeX 命令、公式和引用键，"
                "不得编造数据、作者、DOI 或实验结论；给出可直接采用的修改和理由。\n\n"
                f"项目：{project.name}\n要求：{payload.instruction or '遵循学术规范'}\n\n待处理内容：\n{target}"
            ),
            {"user_id": user.id},
        )
        content = run.output_text
    else:
        content = await research_project_service.manuscript_assist(
            db, project, manuscript, payload.task, payload.selection, payload.instruction
        )
    await audit(
        db,
        "research.manuscript.assisted",
        "research_manuscript",
        manuscript.id,
        {"task": payload.task},
        actor=user.username,
    )
    return {"content": content, "task": payload.task, "source_version": manuscript.version}


@router.post("/research-projects/{project_id}/manuscripts/{manuscript_id}/preview")
async def preview_research_manuscript(
    project_id: str,
    manuscript_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    files = research_project_service.manuscript_files(item)
    return research_project_service.latex_preview(
        research_project_service.flatten_latex(files, item.main_file)
    )


@router.post("/research-projects/{project_id}/manuscripts/{manuscript_id}/compile")
async def compile_research_manuscript(
    project_id: str,
    manuscript_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchManuscript, manuscript_id)
    if not item or item.project_id != project_id:
        raise not_found("论文")
    content, engine_name = await research_project_service.compile_latex(item)
    await audit(
        db,
        "research.manuscript.compiled",
        "research_manuscript",
        item.id,
        {"engine": engine_name},
        actor=user.username,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="manuscript-v{item.version}.pdf"',
            "X-LaTeX-Engine": engine_name,
        },
    )


@router.get("/research-projects/{project_id}/comments")
async def list_research_comments(
    project_id: str,
    manuscript_id: str | None = None,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    query = select(ResearchComment).where(ResearchComment.project_id == project_id)
    if manuscript_id:
        query = query.where(ResearchComment.manuscript_id == manuscript_id)
    items = (await db.scalars(query.order_by(desc(ResearchComment.created_at)))).all()
    result = []
    for item in items:
        account = await db.get(UserAccount, item.author_id) if item.author_id else None
        result.append({**row(item), "author_name": account.display_name if account else "未知成员"})
    return result


@router.post("/research-projects/{project_id}/comments", status_code=201)
async def create_research_comment(
    project_id: str,
    payload: ResearchCommentCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "reviewer")
    if payload.manuscript_id:
        manuscript = await db.get(ResearchManuscript, payload.manuscript_id)
        if not manuscript or manuscript.project_id != project_id:
            raise not_found("论文")
        files = research_project_service.manuscript_files(manuscript)
        if payload.file_path not in files:
            raise HTTPException(status_code=422, detail="批注目标文件不存在")
        record = files[payload.file_path]
        if record.get("encoding") != "utf8":
            raise HTTPException(status_code=422, detail="二进制资源不支持行级批注")
        lines = record.get("content", "").splitlines()
        if payload.line_start and payload.line_start > max(1, len(lines)):
            raise HTTPException(status_code=422, detail="批注起始行超出文件范围")
        values = payload.model_dump()
        values["anchored_version"] = payload.anchored_version or manuscript.version
        if not payload.quote and payload.line_start:
            end = min(payload.line_end or payload.line_start, len(lines))
            values["quote"] = "\n".join(lines[payload.line_start - 1 : end])
    else:
        values = payload.model_dump()
    item = ResearchComment(project_id=project_id, author_id=user.id, **values)
    db.add(item)
    await db.flush()
    return {**row(item), "author_name": user.display_name}


@router.patch("/research-projects/{project_id}/comments/{comment_id}")
async def update_research_comment(
    project_id: str,
    comment_id: str,
    payload: ResearchCommentUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchComment, comment_id)
    if not item or item.project_id != project_id:
        raise not_found("批注")
    item.status = payload.status
    return row(item)


@router.get("/research-projects/{project_id}/reviews")
async def list_research_reviews(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    items = (
        await db.scalars(
            select(ResearchReview)
            .where(ResearchReview.project_id == project_id)
            .order_by(desc(ResearchReview.created_at))
        )
    ).all()
    result = []
    for item in items:
        payload = research_row(item)
        payload["items"] = [
            row(entry)
            for entry in (
                await db.scalars(
                    select(ResearchReviewItem).where(ResearchReviewItem.review_id == item.id)
                )
            ).all()
        ]
        result.append(payload)
    return result


@router.post("/research-projects/{project_id}/reviews", status_code=201)
async def create_research_review(
    project_id: str,
    payload: ResearchReviewCreate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    project, _ = await research_project_service.access(db, project_id, user, "reviewer")
    manuscript = await db.get(ResearchManuscript, payload.manuscript_id)
    if not manuscript or manuscript.project_id != project_id:
        raise not_found("论文")
    prior_rounds = (
        await db.scalars(
            select(ResearchReview).where(ResearchReview.manuscript_id == manuscript.id)
        )
    ).all()
    summary, decision, scores, issues, report = await research_project_service.generate_review(
        db, project, manuscript, payload.roles, payload.venue, payload.rigor, payload.focus
    )
    review = ResearchReview(
        project_id=project_id,
        manuscript_id=manuscript.id,
        created_by=user.id,
        round=len(prior_rounds) + 1,
        roles_json=dumps(payload.roles),
        summary=summary,
        decision=decision,
        scores_json=dumps(scores),
        report_json=dumps(report),
        status="completed",
    )
    db.add(review)
    await db.flush()
    for issue in issues:
        db.add(ResearchReviewItem(review_id=review.id, **issue))
    await db.flush()
    result = research_row(review)
    result["items"] = [
        row(entry)
        for entry in (
            await db.scalars(
                select(ResearchReviewItem).where(ResearchReviewItem.review_id == review.id)
            )
        ).all()
    ]
    return result


@router.post("/research-projects/{project_id}/reviews/stream")
async def stream_research_review(
    project_id: str,
    payload: ResearchReviewCreate,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_committee() -> None:
            try:
                async with session_scope() as db:
                    user = await require_user(db, authorization)
                    project, _ = await research_project_service.access(db, project_id, user, "reviewer")
                    manuscript = await db.get(ResearchManuscript, payload.manuscript_id)
                    if not manuscript or manuscript.project_id != project_id:
                        raise LookupError("论文不存在")
                    prior_rounds = (
                        await db.scalars(
                            select(ResearchReview).where(ResearchReview.manuscript_id == manuscript.id)
                        )
                    ).all()

                    async def progress(event: dict[str, Any]) -> None:
                        await queue.put(event)

                    await queue.put({"type": "committee_started", "total": len(payload.roles)})
                    summary, decision, scores, issues, report = await research_project_service.generate_review(
                        db,
                        project,
                        manuscript,
                        payload.roles,
                        payload.venue,
                        payload.rigor,
                        payload.focus,
                        on_progress=progress,
                    )
                    report["agent_assignments"] = payload.agent_ids
                    review = ResearchReview(
                        project_id=project_id,
                        manuscript_id=manuscript.id,
                        created_by=user.id,
                        round=len(prior_rounds) + 1,
                        roles_json=dumps(payload.roles),
                        summary=summary,
                        decision=decision,
                        scores_json=dumps(scores),
                        report_json=dumps(report),
                        status="completed",
                    )
                    db.add(review)
                    await db.flush()
                    for issue in issues:
                        db.add(ResearchReviewItem(review_id=review.id, **issue))
                    await db.flush()
                    result = research_row(review)
                    result["items"] = [
                        row(entry)
                        for entry in (
                            await db.scalars(
                                select(ResearchReviewItem).where(ResearchReviewItem.review_id == review.id)
                            )
                        ).all()
                    ]
                    await queue.put({"type": "review_result", "review": result})
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc).strip() or "模拟审稿执行失败"})
            finally:
                await queue.put({"type": "done"})

        task = asyncio.create_task(run_committee())
        active_workflow_tasks.add(task)
        task.add_done_callback(active_workflow_tasks.discard)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
            except TimeoutError:
                event = {"type": "review_waiting"}
            yield f"data: {dumps(event)}\n\n"
            if event["type"] == "done":
                break
        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/research-projects/{project_id}/reviews/items/{item_id}")
async def update_research_review_item(
    project_id: str,
    item_id: str,
    payload: ResearchReviewItemUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user, "editor")
    item = await db.get(ResearchReviewItem, item_id)
    review = await db.get(ResearchReview, item.review_id) if item else None
    if not item or not review or review.project_id != project_id:
        raise not_found("审稿意见")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    return row(item)


@router.get("/research-projects/{project_id}/presence")
async def list_research_presence(
    project_id: str,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    return await research_project_service.active_presence(db, project_id)


@router.put("/research-projects/{project_id}/presence")
async def update_research_presence(
    project_id: str,
    payload: ResearchPresenceUpdate,
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    user = await require_user(db, authorization)
    await research_project_service.access(db, project_id, user)
    item = await db.scalar(
        select(ResearchPresence).where(
            ResearchPresence.project_id == project_id, ResearchPresence.user_id == user.id
        )
    )
    if not item:
        item = ResearchPresence(project_id=project_id, user_id=user.id)
        db.add(item)
    item.page = payload.page
    item.cursor_json = dumps(payload.cursor)
    item.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return {**row(item), "display_name": user.display_name, "cursor": payload.cursor}


@router.get("/research-browser/verifications")
async def list_research_verifications() -> list[dict[str, Any]]:
    return web_research_service.active_verifications()


@router.post("/research-browser/verifications/complete")
async def complete_research_verification(
    payload: ResearchVerificationComplete,
) -> dict[str, Any]:
    try:
        return web_research_service.complete_verification(
            payload.verification_id,
            approved=payload.approved,
            url=payload.url,
            cookies=payload.cookies,
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_conversation_message(
    conversation_id: str,
    payload: AgentMessageCreate,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    async with session_scope() as auth_db:
        conversation_owner = await auth_db.get(AgentConversation, conversation_id)
        if conversation_owner is None:
            raise not_found("会话")
        user = await user_service.resolve(auth_db, authorization)
        if user is not None and conversation_owner.user_id not in {None, user.id}:
            raise HTTPException(status_code=403, detail="不能访问其他用户的会话")
        user_id = user.id if user else conversation_owner.user_id
        reply_style_prompt = ""
        if user is not None:
            preference = await user_service.preference(auth_db, user.id)
            reply_style_prompt = user_service.reply_style_prompt(preference)

    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_turn() -> None:
            try:
                async with session_scope() as db:
                    conversation = await db.get(AgentConversation, conversation_id)
                    if not conversation:
                        raise LookupError("会话不存在")
                    if user_id and conversation.user_id is None:
                        conversation.user_id = user_id
                    history_rows = list(
                        (
                            await db.scalars(
                                select(AgentMessage)
                                .where(AgentMessage.conversation_id == conversation_id)
                                .order_by(desc(AgentMessage.created_at))
                                .limit(20)
                            )
                        ).all()
                    )
                    history = [
                        {"role": item.role, "content": item.content}
                        for item in reversed(history_rows)
                        if item.role in {"user", "assistant"}
                    ]
                    user_message = AgentMessage(
                        conversation_id=conversation_id,
                        role="user",
                        content=payload.content,
                    )
                    db.add(user_message)
                    conversation.updated_at = datetime.now(timezone.utc)
                    if conversation.title == "新会话":
                        conversation.title = payload.content.strip()[:36]
                    await db.flush()
                    await user_service.remember_question(
                        db,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        agent_id=conversation.agent_id,
                        question=payload.content,
                    )

                    async def publish_step(event: dict[str, Any]) -> None:
                        if event.get("type") == "run_started" and event.get("run_id"):
                            user_message.run_id = str(event["run_id"])
                            await db.commit()
                        await queue.put({"type": "step", "step": event})

                    result = await agent_engine.run(
                        db,
                        conversation.agent_id,
                        payload.content,
                        user_context={
                            "conversation_id": conversation_id,
                            "security_profile": payload.security_profile,
                            "skill_ids": payload.skill_ids,
                            "user_id": user_id,
                            "reply_style_prompt": reply_style_prompt,
                        },
                        conversation_messages=history,
                        on_event=publish_step,
                    )
                    content = (
                        result.output_text
                        if result.status == "completed"
                        else f"执行失败：{result.error or '未知错误'}"
                    )
                    assistant_message = AgentMessage(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=content,
                        run_id=result.id,
                        trace_json=result.trace_json,
                    )
                    db.add(assistant_message)
                    conversation.updated_at = datetime.now(timezone.utc)
                    await db.flush()
                    persistence_event = await describe_agent_run_persistence(
                        db,
                        conversation=conversation,
                        result=result,
                        assistant_message=assistant_message,
                    )
                    trace = loads(result.trace_json, [])
                    trace.append(persistence_event)
                    result.trace_json = dumps(trace)
                    assistant_message.trace_json = result.trace_json
                    await audit(
                        db,
                        "conversation.turn.completed",
                        "agent_conversation",
                        conversation.id,
                        {"run_id": result.id, "status": result.status},
                        success=result.status == "completed",
                    )
                    await db.commit()
                    await queue.put({"type": "step", "step": persistence_event})
                    await queue.put(
                        {
                            "type": "assistant",
                            "message": row(assistant_message),
                            "run": row(result),
                        }
                    )
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc)})
            finally:
                await queue.put({"type": "done"})

        yield f"data: {dumps({'type': 'step', 'step': {'type': 'stream_connected'}})}\n\n"
        task = asyncio.create_task(run_turn())
        active_conversation_tasks.add(task)
        task.add_done_callback(active_conversation_tasks.discard)
        waiting_seconds = 0
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2)
                    waiting_seconds = 0
                except TimeoutError:
                    waiting_seconds += 2
                    event = {
                        "type": "step",
                        "step": {
                            "type": "model_waiting",
                            "elapsed_seconds": waiting_seconds,
                        },
                    }
                yield f"data: {dumps(event)}\n\n"
                if event["type"] == "done":
                    break
            await task
        finally:
            # A page refresh closes the SSE response, but the persisted Agent run must
            # continue. The task set above keeps it alive; the UI reconnects by polling
            # the run_id stored on the user message.
            pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/agent-runs")
async def list_agent_runs(
    limit: int = 50, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = (
        await db.scalars(
            select(AgentRun).order_by(desc(AgentRun.created_at)).limit(min(limit, 200))
        )
    ).all()
    return [row(item) for item in items]


@router.get("/agent-runs/{run_id}")
async def get_agent_run(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(AgentRun, run_id)
    if not item:
        raise not_found("Agent 运行")
    return row(item)


@router.get("/workflows")
async def list_workflows(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    items = (await db.scalars(select(Workflow).order_by(Workflow.name))).all()
    return [row(item) for item in items]


@router.post("/workflow-clarification")
async def clarify_workflow_requirements(
    payload: WorkflowClarificationRequest,
) -> dict[str, Any]:
    context = {
        "workflow_name": payload.workflow_name,
        "workflow_description": payload.workflow_description,
        "definition": payload.definition,
        "phase": payload.phase,
    }
    if not payload.confirmed:
        return workflow_clarification_service.analyze(payload.task, **context)
    try:
        return workflow_clarification_service.resolve(payload.task, payload.answers, **context)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/workflows", status_code=201)
async def create_workflow(
    payload: WorkflowCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    definition = workflow_engine.normalized_definition(payload.definition)
    try:
        await workflow_engine.validate_runtime_definition(db, definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = Workflow(
        name=payload.name,
        description=payload.description,
        definition_json=dumps(definition),
    )
    db.add(item)
    await db.flush()
    await audit(db, "workflow.created", "workflow", item.id)
    return row(item)


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: str, payload: WorkflowCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(Workflow, workflow_id)
    if not item:
        raise not_found("工作流")
    definition = workflow_engine.normalized_definition(payload.definition)
    try:
        await workflow_engine.validate_runtime_definition(db, definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item.name = payload.name
    item.description = payload.description
    item.definition_json = dumps(definition)
    item.version += 1
    await audit(db, "workflow.updated", "workflow", item.id)
    return row(item)


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str, payload: WorkflowRunRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        result = await workflow_engine.run(
            db,
            workflow_id,
            payload.input,
            run_options={
                "loop_enabled": payload.loop_enabled,
                "loop_count": payload.loop_count,
                "artifact_enabled": payload.artifact_enabled,
                "security_profile": payload.security_profile,
                "permission_mode": payload.permission_mode,
                "approval_policy_id": payload.approval_policy_id,
            },
        )
    except LookupError as exc:
        raise not_found("工作流") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return row(result)


@router.post("/workflows/{workflow_id}/run/stream")
async def stream_workflow_run(workflow_id: str, payload: WorkflowRunRequest) -> StreamingResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_graph() -> None:
            try:
                async with session_scope() as db:

                    async def publish(event: dict[str, Any]) -> None:
                        await queue.put({"type": "step", "step": event})

                    result = await workflow_engine.run(
                        db,
                        workflow_id,
                        payload.input,
                        on_event=publish,
                        run_options={
                            "loop_enabled": payload.loop_enabled,
                            "loop_count": payload.loop_count,
                            "artifact_enabled": payload.artifact_enabled,
                            "security_profile": payload.security_profile,
                            "permission_mode": payload.permission_mode,
                            "approval_policy_id": payload.approval_policy_id,
                        },
                    )
                    await queue.put({"type": "workflow_result", "run": row(result)})
            except Exception as exc:
                message = str(exc).strip() or f"{type(exc).__name__}：工作流执行异常"
                await queue.put({"type": "error", "message": message})
            finally:
                await queue.put({"type": "done"})

        yield f"data: {dumps({'type': 'step', 'step': {'type': 'stream_connected'}})}\n\n"
        task = asyncio.create_task(run_graph())
        active_workflow_tasks.add(task)
        task.add_done_callback(active_workflow_tasks.discard)
        waiting_seconds = 0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
                waiting_seconds = 0
            except TimeoutError:
                waiting_seconds += 2
                event = {
                    "type": "step",
                    "step": {
                        "type": "workflow_waiting",
                        "elapsed_seconds": waiting_seconds,
                    },
                }
            yield f"data: {dumps(event)}\n\n"
            if event["type"] == "done":
                break
        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflow-expert/chat")
async def chat_with_workflow_expert(
    payload: WorkflowExpertChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await workflow_expert.chat(
            db,
            message=payload.message,
            history=payload.history,
            current_definition=payload.current_definition,
            current_agent_drafts=payload.current_agent_drafts,
            workflow_name=payload.workflow_name,
            workflow_description=payload.workflow_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(
        db,
        "workflow.expert.proposed",
        "workflow",
        detail={
            "message": payload.message[:500],
            "node_count": len(result["definition"].get("nodes", [])),
        },
    )
    return result


@router.post("/workflow-expert/materialize")
async def materialize_workflow_expert_proposal(
    payload: WorkflowExpertMaterializeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await workflow_expert.materialize(db, payload.proposal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await audit(
        db,
        "workflow.expert.materialized",
        "workflow",
        detail={
            "created_agent_ids": [item["id"] for item in result["created_agents"]],
            "node_count": len(result["definition"].get("nodes", [])),
        },
    )
    return result


@router.get("/workflow-runs/{run_id}")
async def get_workflow_run(run_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(WorkflowRun, run_id)
    if not item:
        raise not_found("工作流运行")
    return row(item)


@router.get("/workflow-runs/{run_id}/events")
async def get_workflow_run_events(
    run_id: str,
    after: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(WorkflowRun, run_id):
        raise not_found("工作流运行")
    return workflow_engine.events(run_id, max(0, after))


@router.post("/workflow-runs/{run_id}/control")
async def control_workflow_run(
    run_id: str,
    payload: WorkflowRunControlRequest,
) -> dict[str, Any]:
    result = await workflow_engine.control(run_id, payload.action, payload.message)
    if not result["accepted"]:
        raise HTTPException(status_code=409, detail="该工作流当前不在可控制的运行状态")
    return result


@router.get("/workflow-runs/{run_id}/artifacts")
async def list_workflow_artifacts(
    run_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if not await db.get(WorkflowRun, run_id):
        raise not_found("工作流运行")
    items = (
        await db.scalars(
            select(WorkflowArtifact)
            .where(WorkflowArtifact.run_id == run_id)
            .order_by(WorkflowArtifact.iteration, WorkflowArtifact.created_at)
        )
    ).all()
    return [row(item) for item in items]


@router.post("/workflow-artifacts/{artifact_id}/export/docx")
async def export_workflow_artifact_docx(
    artifact_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    artifact = await db.get(WorkflowArtifact, artifact_id)
    if artifact is None:
        raise not_found("工作流产出文档")
    run = await db.get(WorkflowRun, artifact.run_id)
    metadata = loads(artifact.metadata_json, {})
    if not run or run.status != "completed" or metadata.get("delivery_status") == "needs_revision":
        raise HTTPException(
            status_code=409,
            detail="本次运行未通过最终质量校验，不能作为最终成果导出；请修复问题并重新运行。",
        )
    filename = safe_docx_filename(artifact.title)
    return Response(
        content=markdown_to_docx(artifact.title, artifact.content),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post("/workflow-runs/{run_id}/export/docx")
async def export_workflow_run_docx(run_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    run = await db.get(WorkflowRun, run_id)
    if run is None:
        raise not_found("工作流运行")
    if run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail="本次运行未完成或未通过质量校验，不能导出为最终成果。",
        )
    workflow = await db.get(Workflow, run.workflow_id)
    title = f"{workflow.name if workflow else '工作流'}-最终成果"
    # Run exports are clean deliverables: omit workflow metadata, task wrappers,
    # iteration labels, and other orchestration details stored in artifacts.
    markdown = output_to_markdown(run.output_json)
    filename = safe_docx_filename(title)
    return Response(
        content=markdown_to_docx(title, markdown),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/workflow-runs")
async def list_workflow_runs(
    limit: int = 50,
    workflow_id: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(WorkflowRun)
    if workflow_id:
        statement = statement.where(WorkflowRun.workflow_id == workflow_id)
    if status:
        statement = statement.where(WorkflowRun.status == status)
    items = (
        await db.scalars(statement.order_by(desc(WorkflowRun.created_at)).limit(min(limit, 200)))
    ).all()
    return [row(item) for item in items]


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    return tool_runtime.list_tools() + [
        {"name": "call_agent", "description": "调用另一个 Agent", "risk": "low"},
        {
            "name": "web_research",
            "description": "联网检索、抓取公开网页并生成带来源的 Markdown 研究成果",
            "risk": "low",
        },
    ]


@router.get("/approval-policies")
async def list_approval_policies(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    items = (
        await db.scalars(
            select(ApprovalPolicy).order_by(ApprovalPolicy.priority, ApprovalPolicy.name)
        )
    ).all()
    return [row(item) for item in items]


@router.post("/approval-policies", status_code=201)
async def create_approval_policy(
    payload: ApprovalPolicyCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if payload.is_default:
        for current in (
            await db.scalars(select(ApprovalPolicy).where(ApprovalPolicy.is_default.is_(True)))
        ).all():
            current.is_default = False
    item = ApprovalPolicy(
        name=payload.name,
        description=payload.description,
        priority=payload.priority,
        rules_json=dumps(payload.rules),
        enabled=payload.enabled,
        is_default=payload.is_default,
    )
    db.add(item)
    await db.flush()
    await audit(db, "approval_policy.created", "approval_policy", item.id)
    return row(item)


@router.put("/approval-policies/{policy_id}")
async def update_approval_policy(
    policy_id: str, payload: ApprovalPolicyCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(ApprovalPolicy, policy_id)
    if not item:
        raise not_found("审批策略")
    if payload.is_default:
        for current in (
            await db.scalars(
                select(ApprovalPolicy).where(
                    ApprovalPolicy.is_default.is_(True), ApprovalPolicy.id != policy_id
                )
            )
        ).all():
            current.is_default = False
    item.name = payload.name
    item.description = payload.description
    item.priority = payload.priority
    item.rules_json = dumps(payload.rules)
    item.enabled = payload.enabled
    item.is_default = payload.is_default
    await audit(db, "approval_policy.updated", "approval_policy", item.id)
    return row(item)


@router.get("/model-endpoints")
async def list_model_endpoints(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [endpoint_row(item) for item in (await db.scalars(select(ModelEndpoint))).all()]


@router.post("/model-endpoints", status_code=201)
async def create_model_endpoint(
    payload: ModelEndpointCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = ModelEndpoint(
        name=payload.name,
        modality=payload.modality,
        provider_type=payload.provider_type,
        base_url=payload.base_url.rstrip("/"),
        api_key_ciphertext=secret_store.encrypt(payload.api_key),
        default_model=payload.default_model,
        headers_json=dumps(payload.headers),
        request_options_json=dumps(payload.request_options),
        timeout_seconds=payload.timeout_seconds,
        enabled=payload.enabled,
    )
    db.add(item)
    await db.flush()
    if settings.require_online_agents and item.enabled and item.modality == "chat":
        await migrate_agents_to_online_endpoint(db, item)
    await audit(db, "model_endpoint.created", "model_endpoint", item.id)
    return endpoint_row(item)


@router.patch("/model-endpoints/{endpoint_id}")
async def update_model_endpoint(
    endpoint_id: str, payload: ModelEndpointUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(ModelEndpoint, endpoint_id)
    if not item:
        raise not_found("模型接口")
    values = payload.model_dump(exclude_unset=True)
    next_enabled = bool(values.get("enabled", item.enabled))
    next_modality = str(values.get("modality", item.modality))
    replacement = None
    if (
        settings.require_online_agents
        and item.modality == "chat"
        and (not next_enabled or next_modality != "chat")
    ):
        replacement = await latest_chat_endpoint(db, exclude_id=item.id)
        assigned_agents = await db.scalar(
            select(func.count(AgentDefinition.id)).where(
                AgentDefinition.model_endpoint_id == item.id
            )
        )
        if assigned_agents and not replacement:
            raise HTTPException(
                status_code=409,
                detail="该接口仍被 Agent 使用，且没有其他启用的在线对话接口可接管，不能停用",
            )
    if "api_key" in values:
        item.api_key_ciphertext = secret_store.encrypt(values.pop("api_key") or "")
    if "headers" in values:
        item.headers_json = dumps(values.pop("headers"))
    if "request_options" in values:
        item.request_options_json = dumps(values.pop("request_options"))
    for key, value in values.items():
        setattr(item, key, value.rstrip("/") if key == "base_url" else value)
    if settings.require_online_agents and item.enabled and item.modality == "chat":
        await migrate_agents_to_online_endpoint(db, item)
    elif replacement:
        await migrate_agents_to_online_endpoint(db, replacement)
    await audit(db, "model_endpoint.updated", "model_endpoint", item.id)
    return endpoint_row(item)


@router.delete("/model-endpoints/{endpoint_id}", status_code=204)
async def delete_model_endpoint(endpoint_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    item = await db.get(ModelEndpoint, endpoint_id)
    if not item:
        raise not_found("模型接口")

    chat_agents = (
        await db.scalar(
            select(func.count(AgentDefinition.id)).where(
                AgentDefinition.model_endpoint_id == item.id
            )
        )
        or 0
    )
    image_agents = (
        await db.scalar(
            select(func.count(AgentDefinition.id)).where(
                AgentDefinition.image_model_endpoint_id == item.id
            )
        )
        or 0
    )
    knowledge_configs = (
        await db.scalar(
            select(func.count(KnowledgeProviderConfig.id)).where(
                KnowledgeProviderConfig.llm_endpoint_id == item.id
            )
        )
        or 0
    )
    if chat_agents or image_agents or knowledge_configs:
        usages: list[str] = []
        if chat_agents:
            usages.append(f"{chat_agents} 个 Agent 的回答模型")
        if image_agents:
            usages.append(f"{image_agents} 个 Agent 的图片模型")
        if knowledge_configs:
            usages.append("知识库 LLM 配置")
        raise HTTPException(
            status_code=409,
            detail="该接口仍被" + "、".join(usages) + "使用，请先更换相关配置后再删除",
        )

    await audit(
        db,
        "model_endpoint.deleted",
        "model_endpoint",
        item.id,
        {"name": item.name, "modality": item.modality},
    )
    await db.delete(item)
    return Response(status_code=204)


@router.post("/model-endpoints/{endpoint_id}/test")
async def test_model_endpoint(
    endpoint_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(ModelEndpoint, endpoint_id)
    if not item:
        raise not_found("模型接口")
    try:
        if item.modality == "image":
            provider = OpenAICompatibleImageProvider(
                item.base_url,
                secret_store.decrypt(item.api_key_ciphertext),
                headers=loads(item.headers_json, {}),
                request_options=loads(item.request_options_json, {}),
                timeout_seconds=item.timeout_seconds,
            )
            response = await provider.generate(
                "A minimal blue circle on a clean white background",
                model=item.default_model,
            )
            response_preview = response.image_url[:200]
        else:
            provider = OpenAICompatibleProvider(
                item.base_url,
                secret_store.decrypt(item.api_key_ciphertext),
                headers=loads(item.headers_json, {}),
                request_options=loads(item.request_options_json, {}),
                timeout_seconds=item.timeout_seconds,
            )
            health = await provider.health_check(item.default_model)
            response_preview = (
                f"连接正常；模型{'可用' if health['model_available'] else '未在列表中'}；"
                f"共发现 {health['model_count']} 个模型（本次未调用生成接口）"
            )
        item.health = "healthy"
        result = {"status": "healthy", "response": response_preview}
    except Exception as exc:
        item.health = "unhealthy"
        result = {"status": "unhealthy", "error": str(exc)}
    await audit(db, "model_endpoint.tested", "model_endpoint", item.id, result)
    return result


@router.post("/tools/run")
async def run_tool(payload: ToolRunRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        security = await runtime_security_service.resolve(db, payload.security_profile)
        return await tool_runtime.execute(
            db,
            payload.tool,
            payload.arguments,
            run_id=payload.run_id,
            policy_id=payload.policy_id,
            permission_mode=payload.permission_mode,
            security_context=security,
        )
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/approvals")
async def list_approvals(
    status: str | None = None,
    run_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    statement = select(Approval).order_by(desc(Approval.created_at))
    if status:
        statement = statement.where(Approval.status == status)
    if run_id:
        statement = statement.where(Approval.run_id == run_id)
    return [row(item) for item in (await db.scalars(statement.limit(100))).all()]


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str, payload: ApprovalDecision, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(Approval, approval_id)
    if not item:
        raise not_found("审批")
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="审批已处理")
    result: dict[str, Any] | None = None
    if payload.approved and item.action_type.startswith("tool:"):
        stored_payload = loads(item.payload_json, {})
        arguments = stored_payload.get("arguments", stored_payload)
        security_data = stored_payload.get("security_context")
        security = (
            RuntimeSecurityContext(**security_data)
            if isinstance(security_data, dict)
            else await runtime_security_service.resolve(db)
        )
        result = await tool_runtime.execute(
            db,
            item.action_type.split(":", 1)[1],
            arguments,
            run_id=item.run_id,
            permission_mode="auto",
            preapproved=True,
            security_context=security,
        )
        item.execution_result_json = dumps(result)
    item.status = "approved" if payload.approved else "rejected"
    item.decided_by = payload.decided_by
    item.decided_at = datetime.now(timezone.utc)
    await audit(
        db,
        "approval.decided",
        "approval",
        item.id,
        {"approved": payload.approved},
        actor=payload.decided_by,
    )
    return {**row(item), "execution_result": result}


@router.get("/knowledge-bases")
async def list_knowledge_bases(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in (await db.scalars(select(KnowledgeBase))).all()]


async def _knowledge_group_row(db: AsyncSession, group: KnowledgeBaseGroup) -> dict[str, Any]:
    base_ids = list(
        (
            await db.scalars(
                select(KnowledgeBaseGroupMember.knowledge_base_id).where(
                    KnowledgeBaseGroupMember.group_id == group.id
                )
            )
        ).all()
    )
    return {**row(group), "knowledge_base_ids": base_ids, "knowledge_base_count": len(base_ids)}


@router.get("/knowledge-groups")
async def list_knowledge_groups(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    groups = (await db.scalars(select(KnowledgeBaseGroup).order_by(KnowledgeBaseGroup.name))).all()
    return [await _knowledge_group_row(db, group) for group in groups]


@router.post("/knowledge-groups", status_code=201)
async def create_knowledge_group(
    payload: KnowledgeBaseGroupCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if await db.scalar(
        select(KnowledgeBaseGroup.id).where(KnowledgeBaseGroup.name == payload.name)
    ):
        raise HTTPException(status_code=409, detail="知识库分组名称已存在")
    base_ids = list(dict.fromkeys(payload.knowledge_base_ids))
    if base_ids:
        existing = set(
            (await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(base_ids)))).all()
        )
        if existing != set(base_ids):
            raise HTTPException(status_code=400, detail="分组中包含不存在的知识库")
    group = KnowledgeBaseGroup(
        name=payload.name,
        description=payload.description,
        color=payload.color,
    )
    db.add(group)
    await db.flush()
    for base_id in base_ids:
        db.add(KnowledgeBaseGroupMember(group_id=group.id, knowledge_base_id=base_id))
    await db.flush()
    await audit(
        db,
        "knowledge.group_created",
        "knowledge_base_group",
        group.id,
        {"knowledge_base_count": len(base_ids)},
    )
    result = await _knowledge_group_row(db, group)
    await db.commit()
    return result


@router.patch("/knowledge-groups/{group_id}")
async def update_knowledge_group(
    group_id: str,
    payload: KnowledgeBaseGroupUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    group = await db.get(KnowledgeBaseGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="知识库分组不存在")
    if payload.name and await db.scalar(
        select(KnowledgeBaseGroup.id).where(
            KnowledgeBaseGroup.name == payload.name,
            KnowledgeBaseGroup.id != group_id,
        )
    ):
        raise HTTPException(status_code=409, detail="知识库分组名称已存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(group, key, value)
    await db.flush()
    await audit(db, "knowledge.group_updated", "knowledge_base_group", group.id)
    result = await _knowledge_group_row(db, group)
    await db.commit()
    return result


@router.put("/knowledge-groups/{group_id}/members")
async def update_knowledge_group_members(
    group_id: str,
    payload: KnowledgeBaseGroupMembersUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    group = await db.get(KnowledgeBaseGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="知识库分组不存在")
    base_ids = list(dict.fromkeys(payload.knowledge_base_ids))
    existing = (
        set(
            (await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(base_ids)))).all()
        )
        if base_ids
        else set()
    )
    if existing != set(base_ids):
        raise HTTPException(status_code=400, detail="分组中包含不存在的知识库")
    await db.execute(
        delete(KnowledgeBaseGroupMember).where(KnowledgeBaseGroupMember.group_id == group_id)
    )
    for base_id in base_ids:
        db.add(KnowledgeBaseGroupMember(group_id=group_id, knowledge_base_id=base_id))
    await db.flush()
    await audit(
        db,
        "knowledge.group_members_updated",
        "knowledge_base_group",
        group.id,
        {"knowledge_base_count": len(base_ids)},
    )
    result = await _knowledge_group_row(db, group)
    await db.commit()
    return result


@router.delete("/knowledge-groups/{group_id}", status_code=204)
async def delete_knowledge_group(group_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    group = await db.get(KnowledgeBaseGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="知识库分组不存在")
    await db.delete(group)
    await audit(db, "knowledge.group_deleted", "knowledge_base_group", group_id)
    await db.commit()
    return Response(status_code=204)


@router.post("/knowledge-bases", status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = KnowledgeBase(**payload.model_dump())
    db.add(item)
    await db.flush()
    await audit(db, "knowledge.created", "knowledge_base", item.id)
    await db.commit()
    return row(item)


@router.patch("/knowledge-bases/{knowledge_base_id}")
async def update_knowledge_base(
    knowledge_base_id: str,
    payload: KnowledgeBaseUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(KnowledgeBase, knowledge_base_id)
    if not item:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if payload.name and await db.scalar(
        select(KnowledgeBase.id).where(
            KnowledgeBase.name == payload.name,
            KnowledgeBase.id != knowledge_base_id,
        )
    ):
        raise HTTPException(status_code=409, detail="知识库名称已存在")
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(item, key, value)
    await audit(db, "knowledge.updated", "knowledge_base", item.id)
    await db.commit()
    return row(item)


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=204)
async def delete_knowledge_base(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    item = await db.get(KnowledgeBase, knowledge_base_id)
    if not item:
        raise HTTPException(status_code=404, detail="知识库不存在")
    document_ids = list(
        (
            await db.scalars(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.knowledge_base_id == knowledge_base_id
                )
            )
        ).all()
    )
    for document_id in document_ids:
        await knowledge_service.delete_document(db, document_id, audit_event=False)
    await db.execute(
        delete(KnowledgeIngestionJob).where(
            KnowledgeIngestionJob.knowledge_base_id == knowledge_base_id
        )
    )
    await db.execute(
        delete(KnowledgeSource).where(KnowledgeSource.knowledge_base_id == knowledge_base_id)
    )
    await db.execute(
        delete(KnowledgeBaseGroupMember).where(
            KnowledgeBaseGroupMember.knowledge_base_id == knowledge_base_id
        )
    )
    await db.delete(item)
    await audit(
        db,
        "knowledge.deleted",
        "knowledge_base",
        knowledge_base_id,
        {"documents": len(document_ids)},
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/knowledge-bases/{knowledge_base_id}/documents")
async def list_documents(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = (
        await db.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id
            )
        )
    ).all()
    return [row(item) for item in items]


@router.get("/knowledge-bases/{knowledge_base_id}/overview")
async def knowledge_base_overview(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    knowledge_base = await db.get(KnowledgeBase, knowledge_base_id)
    if not knowledge_base:
        raise HTTPException(status_code=404, detail="知识库不存在")
    config = await get_knowledge_config(db)
    level_rows = (
        await db.execute(
            select(KnowledgeChunk.level, func.count(KnowledgeChunk.id))
            .where(KnowledgeChunk.knowledge_base_id == knowledge_base_id)
            .group_by(KnowledgeChunk.level)
        )
    ).all()
    level_counts = {str(level): int(count) for level, count in level_rows}
    embedding_rows = (
        await db.execute(
            select(
                KnowledgeEmbedding.provider,
                KnowledgeEmbedding.model,
                KnowledgeEmbedding.dimensions,
                func.count(KnowledgeEmbedding.chunk_id),
            )
            .where(KnowledgeEmbedding.knowledge_base_id == knowledge_base_id)
            .group_by(
                KnowledgeEmbedding.provider,
                KnowledgeEmbedding.model,
                KnowledgeEmbedding.dimensions,
            )
        )
    ).all()
    source_rows = (
        await db.execute(
            select(
                KnowledgeSource.source_type, KnowledgeSource.status, func.count(KnowledgeSource.id)
            )
            .where(KnowledgeSource.knowledge_base_id == knowledge_base_id)
            .group_by(KnowledgeSource.source_type, KnowledgeSource.status)
        )
    ).all()
    return {
        "knowledge_base": row(knowledge_base),
        "statistics": {
            "documents": int(
                await db.scalar(
                    select(func.count(KnowledgeDocument.id)).where(
                        KnowledgeDocument.knowledge_base_id == knowledge_base_id
                    )
                )
                or 0
            ),
            "parent_chunks": level_counts.get("parent", 0),
            "child_chunks": level_counts.get("child", 0),
            "embeddings": sum(int(item[3]) for item in embedding_rows),
            "sources": sum(int(item[2]) for item in source_rows),
        },
        "source_summary": [
            {"source_type": item[0], "status": item[1], "count": int(item[2])}
            for item in source_rows
        ],
        "vector_indexes": [
            {
                "provider": item[0],
                "model": item[1],
                "dimensions": item[2],
                "count": int(item[3]),
            }
            for item in embedding_rows
        ],
        "retrieval_strategy": {
            "query_rewrite": "LLM 多查询改写；未配置生成模型时进行术语规范化",
            "retrievers": ["Dense cosine", "SQLite FTS5 BM25", "中文二元词项覆盖"],
            "fusion": "Reciprocal Rank Fusion (RRF, k=60)",
            "candidate_k": config.candidate_k,
            "rerank_model": config.rerank_model,
            "top_k": config.top_k,
            "diversity": "内容哈希去重，每份文档最多 3 个最终片段",
            "context_expansion": "命中子块后扩展到父块",
            "context_char_budget": config.context_char_budget,
            "citation_policy": "资料编号 + 文档 + 页码/幻灯片/章节 + 原始来源",
        },
    }


@router.get("/knowledge-documents/{document_id}")
async def get_knowledge_document(
    document_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    document = await db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="知识文档不存在")
    parents = (
        await db.scalars(
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id == document_id,
                KnowledgeChunk.level == "parent",
            )
            .order_by(KnowledgeChunk.chunk_index)
        )
    ).all()
    content_chunks = parents
    if not content_chunks:
        # Knowledge bases created before hierarchical chunking only contain child chunks.
        content_chunks = (
            await db.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document_id)
                .order_by(KnowledgeChunk.chunk_index)
            )
        ).all()
    child_count = int(
        await db.scalar(
            select(func.count(KnowledgeChunk.id)).where(
                KnowledgeChunk.document_id == document_id,
                KnowledgeChunk.level == "child",
            )
        )
        or 0
    )
    embedding_count = int(
        await db.scalar(
            select(func.count(KnowledgeEmbedding.chunk_id))
            .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeEmbedding.chunk_id)
            .where(KnowledgeChunk.document_id == document_id)
        )
        or 0
    )
    data = row(document)
    data.update(
        {
            "metadata": loads(document.metadata_json, {}),
            "cleaning_stats": loads(document.cleaning_stats_json, {}),
            "cleaned_content": "\n\n".join(item.content for item in content_chunks),
            "parent_chunk_count": len(parents),
            "child_chunk_count": child_count,
            "embedding_count": embedding_count,
        }
    )
    return data


@router.get("/knowledge-documents/{document_id}/chunks")
async def list_knowledge_document_chunks(
    document_id: str,
    level: str = Query(default="all", pattern="^(all|parent|child)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(KnowledgeDocument, document_id):
        raise HTTPException(status_code=404, detail="知识文档不存在")
    filters = [KnowledgeChunk.document_id == document_id]
    if level != "all":
        filters.append(KnowledgeChunk.level == level)
    total = int(await db.scalar(select(func.count(KnowledgeChunk.id)).where(*filters)) or 0)
    rows = (
        await db.execute(
            select(KnowledgeChunk, KnowledgeEmbedding)
            .outerjoin(KnowledgeEmbedding, KnowledgeEmbedding.chunk_id == KnowledgeChunk.id)
            .where(*filters)
            .order_by(KnowledgeChunk.chunk_index)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                **row(chunk),
                "metadata": loads(chunk.metadata_json, {}),
                "embedding": (
                    {
                        "indexed": True,
                        "provider": embedding.provider,
                        "model": embedding.model,
                        "dimensions": embedding.dimensions,
                        "content_hash": embedding.content_hash,
                    }
                    if embedding
                    else {"indexed": False}
                ),
            }
            for chunk, embedding in rows
        ],
    }


@router.patch("/knowledge-documents/{document_id}")
async def update_knowledge_document(
    document_id: str,
    payload: KnowledgeDocumentUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        document = await knowledge_service.update_document(
            db,
            document_id,
            title=payload.title,
            source=payload.source,
            content=payload.content,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await db.commit()
    return row(document)


@router.delete("/knowledge-documents/{document_id}", status_code=204)
async def delete_knowledge_document(
    document_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    try:
        await knowledge_service.delete_document(db, document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return Response(status_code=204)


@router.post("/knowledge-bases/{knowledge_base_id}/documents/text", status_code=201)
async def add_text_document(
    knowledge_base_id: str,
    payload: TextDocumentCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        item = await knowledge_service.add_document(
            db,
            knowledge_base_id,
            title=payload.title,
            content=payload.content,
            source=payload.source,
        )
    except LookupError as exc:
        raise not_found("知识库") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await db.commit()
    return row(item)


@router.post("/knowledge-bases/{knowledge_base_id}/documents/upload", status_code=201)
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    relative_path: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 25MB")
    try:
        filename = file.filename or "document.txt"
        path_parts = [
            part.strip()
            for part in relative_path.replace("\\", "/").split("/")[:-1]
            if part.strip() not in {"", ".", ".."}
        ]
        display_path = "/".join([*path_parts, filename])[-2000:]
        sections, mime = extract_sections(filename, data)
        source = await knowledge_source_service.create(
            db,
            knowledge_base_id,
            name=display_path,
            source_type="file",
            uri=display_path,
            config={"filename": filename, "relative_path": display_path},
        )
        item, result = await knowledge_service.add_sections(
            db,
            knowledge_base_id,
            title=display_path,
            sections=sections,
            source=f"本地文件：{display_path}",
            mime_type=mime,
            source_id=source.id,
            metadata={
                "filename": filename,
                "relative_path": display_path,
                "source_type": "file",
            },
        )
        source.status = "ready"
        source.last_synced_at = datetime.now(timezone.utc)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=400, detail="文档清洗后没有可用内容")
    if result.get("duplicate"):
        await db.delete(source)
        await db.commit()
        return {**row(item), "ingestion": result, "source_id": item.source_id}
    await db.commit()
    return {**row(item), "ingestion": result, "source_id": source.id}


@router.post("/knowledge/search")
async def search_knowledge(
    payload: KnowledgeSearchRequest, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    return await knowledge_service.search(
        db,
        payload.query,
        payload.knowledge_base_ids,
        payload.top_k,
        payload.knowledge_group_ids,
    )


@router.post("/knowledge/query")
async def query_knowledge(
    payload: KnowledgeQueryRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        return await knowledge_service.query(
            db,
            query=payload.query,
            knowledge_base_ids=payload.knowledge_base_ids,
            knowledge_group_ids=payload.knowledge_group_ids,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k,
            generate_answer=payload.generate_answer,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/knowledge/query/stream")
async def stream_knowledge_query(payload: KnowledgeQueryRequest) -> StreamingResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_query() -> None:
            try:
                async with session_scope() as db:

                    async def publish(event: dict[str, Any]) -> None:
                        await queue.put({"type": "step", "step": event})

                    result = await knowledge_service.query(
                        db,
                        query=payload.query,
                        knowledge_base_ids=payload.knowledge_base_ids,
                        knowledge_group_ids=payload.knowledge_group_ids,
                        top_k=payload.top_k,
                        candidate_k=payload.candidate_k,
                        generate_answer=payload.generate_answer,
                        on_event=publish,
                    )
                    await queue.put({"type": "knowledge_result", "result": result})
            except Exception as exc:
                message = str(exc).strip() or f"{type(exc).__name__}：知识检索异常"
                await queue.put({"type": "error", "message": message})
            finally:
                await queue.put({"type": "done"})

        yield f"data: {dumps({'type': 'step', 'step': {'type': 'stream_connected'}})}\n\n"
        task = asyncio.create_task(run_query())
        active_knowledge_tasks.add(task)
        task.add_done_callback(active_knowledge_tasks.discard)
        waiting_seconds = 0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
                waiting_seconds = 0
            except TimeoutError:
                waiting_seconds += 2
                event = {
                    "type": "step",
                    "step": {
                        "type": "knowledge_waiting",
                        "elapsed_seconds": waiting_seconds,
                    },
                }
            yield f"data: {dumps(event)}\n\n"
            if event["type"] == "done":
                break
        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/knowledge/config")
async def get_knowledge_provider_config(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return knowledge_config_row(await get_knowledge_config(db))


@router.put("/knowledge/config")
async def update_knowledge_provider_config(
    payload: KnowledgeProviderConfigUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    config = await get_knowledge_config(db)
    changes = payload.model_dump(exclude_unset=True)
    api_key = changes.pop("api_key", None)
    if api_key is not None:
        config.api_key_ciphertext = secret_store.encrypt(api_key)
    if "llm_endpoint_id" in changes and changes["llm_endpoint_id"]:
        if not await db.get(ModelEndpoint, changes["llm_endpoint_id"]):
            raise HTTPException(status_code=400, detail="指定的大模型端点不存在")
    for key, value in changes.items():
        if value is not None:
            setattr(config, key, value)
    await db.flush()
    await audit(db, "knowledge.config_updated", "knowledge_provider_config", config.id)
    await db.commit()
    return knowledge_config_row(config)


@router.post("/knowledge/config/test")
async def test_knowledge_provider_config(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    config = await get_knowledge_config(db)
    try:
        embedder = EmbeddingClient(config)
        vectors = await embedder.embed(["EvoAgent 知识库连接测试"])
        reranked = await RerankClient(config).rerank("网格质量", ["天气预报", "网格质量评价"], 1)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "status": "healthy" if embedder.provider_name == "siliconflow" else "degraded",
        "embedding_provider": embedder.provider_name,
        "embedding_model": embedder.model,
        "dimensions": len(vectors[0]),
        "rerank_model": config.rerank_model,
        "rerank_top_index": reranked[0][0] if reranked else None,
    }


async def _create_and_optionally_sync_source(
    db: AsyncSession,
    knowledge_base_id: str,
    *,
    name: str,
    source_type: str,
    uri: str,
    config: dict[str, Any],
    sync_now: bool,
) -> dict[str, Any]:
    source = await knowledge_source_service.create(
        db,
        knowledge_base_id,
        name=name,
        source_type=source_type,
        uri=uri,
        config=config,
    )
    result: dict[str, Any] = {"source": knowledge_source_service.public_row(source), "job": None}
    if sync_now:
        try:
            job = await knowledge_source_service.sync(db, source.id)
            result["job"] = row(job)
        except Exception as exc:
            result["sync_error"] = str(exc)
            result["source"] = knowledge_source_service.public_row(source)
    await db.commit()
    return result


@router.get("/knowledge-bases/{knowledge_base_id}/sources")
async def list_knowledge_sources(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = await knowledge_source_service.list_for_base(db, knowledge_base_id)
    return [knowledge_source_service.public_row(item) for item in items]


@router.patch("/knowledge-sources/{source_id}")
async def update_knowledge_source(
    source_id: str,
    payload: KnowledgeSourceUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        source = await knowledge_source_service.update(
            db,
            source_id,
            name=payload.name,
            uri=payload.uri,
            config=payload.config,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return knowledge_source_service.public_row(source)


@router.delete("/knowledge-sources/{source_id}", status_code=204)
async def delete_knowledge_source(
    source_id: str,
    delete_documents: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> Response:
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    documents = (
        await db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.source_id == source_id))
    ).all()
    if delete_documents:
        for document in documents:
            await knowledge_service.delete_document(db, document.id)
    else:
        await db.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.source_id == source_id)
            .values(source_id=None)
        )
    await db.execute(
        update(KnowledgeIngestionJob)
        .where(KnowledgeIngestionJob.source_id == source_id)
        .values(source_id=None)
    )
    await db.delete(source)
    await audit(
        db,
        "knowledge.source_deleted",
        "knowledge_source",
        source_id,
        {"documents_deleted": len(documents) if delete_documents else 0},
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/knowledge-bases/{knowledge_base_id}/sources/web", status_code=201)
async def create_web_knowledge_source(
    knowledge_base_id: str,
    payload: WebKnowledgeSourceCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await _create_and_optionally_sync_source(
            db,
            knowledge_base_id,
            name=payload.name,
            source_type="web",
            uri=payload.url,
            config=payload.model_dump(exclude={"name", "sync_now"}),
            sync_now=payload.sync_now,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge-bases/{knowledge_base_id}/sources/database", status_code=201)
async def create_database_knowledge_source(
    knowledge_base_id: str,
    payload: DatabaseKnowledgeSourceCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await _create_and_optionally_sync_source(
            db,
            knowledge_base_id,
            name=payload.name,
            source_type="database",
            uri=payload.connection_url,
            config=payload.model_dump(exclude={"name", "sync_now"}),
            sync_now=payload.sync_now,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge-bases/{knowledge_base_id}/sources/api", status_code=201)
async def create_api_knowledge_source(
    knowledge_base_id: str,
    payload: APIKnowledgeSourceCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        return await _create_and_optionally_sync_source(
            db,
            knowledge_base_id,
            name=payload.name,
            source_type="api",
            uri=payload.url,
            config=payload.model_dump(exclude={"name", "sync_now"}),
            sync_now=payload.sync_now,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge-sources/{source_id}/sync")
async def sync_knowledge_source(
    source_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        job = await knowledge_source_service.sync(db, source_id)
        await db.commit()
        return row(job)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        source = await db.get(KnowledgeSource, source_id)
        await db.commit()
        return {
            "status": "failed",
            "source_id": source_id,
            "error": str(exc),
            "source": knowledge_source_service.public_row(source) if source else None,
        }


@router.get("/knowledge-ingestion-jobs/{job_id}")
async def get_knowledge_ingestion_job(
    job_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    job = await db.get(KnowledgeIngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="导入任务不存在")
    return row(job)


@router.post("/knowledge-bases/{knowledge_base_id}/reindex")
async def reindex_knowledge_base(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if not await db.get(KnowledgeBase, knowledge_base_id):
        raise HTTPException(status_code=404, detail="知识库不存在")
    try:
        result = await knowledge_service.reindex(db, knowledge_base_id)
        await db.commit()
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/mcp/workspace")
async def workspace_mcp(
    payload: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    method = payload.get("method")
    allowed_tools = {"list_directory", "read_file", "search_files"}
    if method == "initialize":
        return mcp_response(
            payload,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "EvoAgent Workspace MCP", "version": settings.version},
            },
        )
    if method == "tools/list":
        tools = [
            item for item in tool_runtime.schemas() if item["function"]["name"] in allowed_tools
        ]
        return mcp_response(
            payload,
            {
                "tools": [
                    {
                        "name": item["function"]["name"],
                        "description": item["function"]["description"],
                        "inputSchema": item["function"]["parameters"],
                    }
                    for item in tools
                ]
            },
        )
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        if name not in allowed_tools:
            return mcp_error(payload, -32601, "工作区 MCP 不允许此工具")
        security = await runtime_security_service.resolve(db)
        result = await tool_runtime.execute(
            db,
            name,
            dict(params.get("arguments") or {}),
            permission_mode="auto",
            security_context=security,
        )
        return mcp_response(
            payload,
            {
                "content": [{"type": "text", "text": dumps(result)}],
                "structuredContent": result,
            },
        )
    return mcp_error(payload, -32601, "不支持的 MCP 方法")


@router.post("/mcp/knowledge")
async def knowledge_mcp(
    payload: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    method = payload.get("method")
    if method == "initialize":
        return mcp_response(
            payload,
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "EvoAgent Knowledge MCP", "version": settings.version},
            },
        )
    if method == "tools/list":
        return mcp_response(
            payload,
            {
                "tools": [
                    {
                        "name": "knowledge_bases_list",
                        "description": "列出 EvoAgent 学科知识库",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "knowledge_search",
                        "description": "检索学科知识并返回可追溯引用",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "knowledge_base_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "knowledge_group_ids": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                            },
                            "required": ["query"],
                        },
                    },
                ]
            },
        )
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        arguments = dict(params.get("arguments") or {})
        if name == "knowledge_bases_list":
            items = (await db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name))).all()
            result: Any = [row(item) for item in items]
        elif name == "knowledge_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return mcp_error(payload, -32602, "query 不能为空")
            result = await knowledge_service.search(
                db,
                query,
                list(arguments.get("knowledge_base_ids") or []),
                min(max(int(arguments.get("top_k") or 5), 1), 20),
                list(arguments.get("knowledge_group_ids") or []),
            )
        else:
            return mcp_error(payload, -32601, "知识库 MCP 不支持此工具")
        return mcp_response(
            payload,
            {
                "content": [{"type": "text", "text": dumps(result)}],
                "structuredContent": result,
            },
        )
    return mcp_error(payload, -32601, "不支持的 MCP 方法")


@router.get("/skills")
async def list_skills(
    verified_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    query = select(Skill)
    if verified_only:
        query = query.where(
            Skill.enabled.is_(True),
            Skill.validation_status == "verified",
        )
    return [row(item) for item in (await db.scalars(query.order_by(Skill.name))).all()]


@router.post("/skills", status_code=201)
async def create_skill(payload: SkillCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if await db.scalar(select(Skill).where(Skill.name == payload.name)):
        raise HTTPException(status_code=409, detail="Skill 名称已存在")
    content = (
        "---\n"
        f"name: {dumps(payload.name)}\n"
        f"description: {dumps(payload.description)}\n"
        "---\n\n"
        f"{payload.instructions.strip()}\n"
    ).encode("utf-8")
    files, report = skill_security_service.validate_upload("SKILL.md", content)
    if report["status"] != "verified":
        raise HTTPException(
            status_code=422,
            detail={"message": "Skill 未通过格式或安全校验", "report": report},
        )
    try:
        source_path = skill_security_service.install_verified(files, report)
    except (SkillPackageError, FileExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item = Skill(
        name=payload.name,
        description=payload.description,
        instructions=payload.instructions,
        version=payload.version,
        source_path=str(source_path),
    )
    skill_security_service.apply_report(item, report)
    db.add(item)
    await db.flush()
    await audit(db, "skill.created", "skill", item.id)
    return row(item)


@router.post("/skills/upload")
async def upload_skill(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    payload = await file.read(6 * 1024 * 1024 + 1)
    try:
        files, report = skill_security_service.validate_upload(file.filename or "", payload)
    except SkillPackageError as exc:
        report = exc.report or {
            "is_skill": False,
            "safe": False,
            "status": "rejected",
            "risk_level": "high",
            "checks": {},
            "findings": [
                {
                    "severity": "high",
                    "code": "package-invalid",
                    "message": str(exc),
                    "path": file.filename or "upload",
                    "line": None,
                }
            ],
            "files": [],
        }
        await audit(
            db,
            "skill.upload_rejected",
            "skill",
            detail={"filename": file.filename, "report": report},
            success=False,
        )
        return {"accepted": False, "report": report}
    if report["status"] != "verified":
        await audit(
            db,
            "skill.upload_rejected",
            "skill",
            detail={"filename": file.filename, "report": report},
            success=False,
        )
        return {"accepted": False, "report": report}
    metadata = report["metadata"]
    if await db.scalar(select(Skill).where(Skill.name == metadata["name"])):
        raise HTTPException(status_code=409, detail="同名 Skill 已存在")
    try:
        source_path = skill_security_service.install_verified(files, report)
    except (SkillPackageError, FileExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    item = Skill(
        name=metadata["name"],
        description=metadata["description"],
        instructions=report["instructions"],
        version=metadata["version"],
        source_path=str(source_path),
    )
    skill_security_service.apply_report(item, report)
    db.add(item)
    await db.flush()
    await audit(
        db,
        "skill.upload_verified",
        "skill",
        item.id,
        {"filename": file.filename, "content_hash": item.content_hash},
    )
    return {"accepted": True, "skill": row(item), "report": report}


@router.post("/skills/sync")
async def sync_skills(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in await extension_service.sync_skills(db)]


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(Skill, skill_id)
    if not item:
        raise not_found("Skill")
    data = row(item)
    data["validation_report"] = loads(item.validation_json, {})
    data["files"] = []
    source = Path(item.source_path) if item.source_path and "://" not in item.source_path else None
    if source and source.exists():
        root = source.parent
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                content = path.read_text("utf-8")
            except (UnicodeError, OSError):
                content = ""
            data["files"].append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "content": content[:100_000],
                }
            )
    return data


@router.post("/skills/{skill_id}/validate")
async def validate_skill(skill_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(Skill, skill_id)
    if not item:
        raise not_found("Skill")
    if item.source_path.startswith("builtin://"):
        return await get_skill(skill_id, db)
    source = Path(item.source_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Skill 源文件不存在")
    try:
        report = skill_security_service.validate_directory(source.parent)
    except SkillPackageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata = report.get("metadata") or {}
    item.description = str(metadata.get("description") or item.description)
    item.instructions = str(report.get("instructions") or item.instructions)
    item.version = str(metadata.get("version") or item.version)
    skill_security_service.apply_report(item, report)
    await audit(
        db,
        "skill.validated",
        "skill",
        item.id,
        {"status": item.validation_status, "risk_level": item.risk_level},
        success=item.validation_status == "verified",
    )
    return await get_skill(skill_id, db)


@router.get("/extensions")
async def list_extensions(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in (await db.scalars(select(Extension))).all()]


@router.post("/extensions", status_code=201)
async def create_extension(
    payload: ExtensionCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = Extension(
        name=payload.name,
        kind=payload.kind,
        description=payload.description,
        config_json=dumps(payload.config),
        permissions_json=dumps(payload.permissions),
    )
    db.add(item)
    await db.flush()
    await audit(db, "extension.created", "extension", item.id)
    return row(item)


@router.post("/extensions/sync-plugins")
async def sync_plugins(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in await extension_service.sync_plugins(db)]


@router.post("/extensions/{extension_id}/test")
async def test_extension(extension_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(Extension, extension_id)
    if not item:
        raise not_found("扩展")
    result = await extension_service.test_connection(item)
    await audit(db, "extension.tested", "extension", item.id, result)
    return result


@router.get("/extensions/{extension_id}/tools")
async def list_extension_tools(
    extension_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(Extension, extension_id)
    if not item:
        raise not_found("扩展")
    return await extension_service.list_mcp_tools(item)


@router.post("/extensions/{extension_id}/tools/{tool_name}")
async def call_extension_tool(
    extension_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(Extension, extension_id)
    if not item:
        raise not_found("扩展")
    security = await runtime_security_service.resolve(db)
    return await extension_service.call_mcp_tool(
        item, tool_name, arguments, db=db, security_context=security
    )


@router.get("/evaluation-cases")
async def list_evaluation_cases(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in (await db.scalars(select(EvaluationCase))).all()]


@router.post("/evaluation-cases", status_code=201)
async def create_evaluation_case(
    payload: EvaluationCaseCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = EvaluationCase(
        name=payload.name,
        discipline=payload.discipline,
        category=payload.category,
        input_text=payload.input,
        expected_keywords_json=dumps(payload.expected_keywords),
        requires_citation=payload.requires_citation,
        weight=payload.weight,
        enabled=payload.enabled,
    )
    db.add(item)
    await db.flush()
    await audit(db, "evaluation_case.created", "evaluation_case", item.id)
    await db.commit()
    await db.refresh(item)
    return row(item)


@router.put("/evaluation-cases/{case_id}")
async def update_evaluation_case(
    case_id: str,
    payload: EvaluationCaseUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(EvaluationCase, case_id)
    if not item:
        raise not_found("评测用例")
    changes = payload.model_dump(exclude_unset=True)
    field_map = {"input": "input_text", "expected_keywords": "expected_keywords_json"}
    for key, value in changes.items():
        target = field_map.get(key, key)
        setattr(item, target, dumps(value) if key == "expected_keywords" else value)
    await audit(db, "evaluation_case.updated", "evaluation_case", item.id)
    await db.commit()
    await db.refresh(item)
    return row(item)


@router.delete("/evaluation-cases/{case_id}", status_code=204)
async def delete_evaluation_case(case_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    item = await db.get(EvaluationCase, case_id)
    if not item:
        raise not_found("评测用例")
    await db.delete(item)
    await audit(db, "evaluation_case.deleted", "evaluation_case", case_id)
    await db.commit()
    return Response(status_code=204)


@router.get("/evolution/overview")
async def evolution_overview(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await evolution_service.overview(db)


@router.post("/evolution/analyze-goal")
async def analyze_evolution_goal(
    payload: EvolutionGoalAnalyze,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    source = await db.get(AgentDefinition, payload.agent_id)
    if not source:
        raise not_found("Agent")
    return await evolution_service.analyze_goal(
        db, source, payload.goal, payload.include_run_insights
    )


@router.get("/evolution/lineages")
async def evolution_lineages(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    agents = list(
        (
            await db.scalars(
                select(AgentDefinition).order_by(
                    AgentDefinition.lineage_id, desc(AgentDefinition.version)
                )
            )
        ).all()
    )
    grouped: dict[str, list[AgentDefinition]] = {}
    for agent in agents:
        grouped.setdefault(agent.lineage_id, []).append(agent)
    return [
        {
            "lineage_id": lineage_id,
            "name": versions[0].name,
            "active_agent_id": next(
                (item.id for item in versions if item.status == "active"), None
            ),
            "versions": [row(item) for item in versions],
        }
        for lineage_id, versions in grouped.items()
        if len(versions) > 1
    ]


@router.post("/evolution/agents/{agent_id}/rollback")
async def rollback_evolution_agent(
    agent_id: str,
    payload: EvolutionRollback,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    active = await db.get(AgentDefinition, agent_id)
    target = await db.get(AgentDefinition, payload.target_agent_id)
    if not active or not target:
        raise not_found("Agent 版本")
    try:
        result = await evolution_service.rollback(db, active, target, payload.reason, payload.actor)
        await db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/evolution")
async def list_evolution(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        row(item)
        for item in (
            await db.scalars(select(EvolutionProposal).order_by(desc(EvolutionProposal.created_at)))
        ).all()
    ]


@router.post("/evolution", status_code=201)
async def create_evolution(
    payload: EvolutionCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    source = await db.get(AgentDefinition, payload.agent_id)
    if not source:
        raise not_found("Agent")
    item = await evolution_service.create_proposal(
        db,
        source,
        payload.reason,
        payload.proposed_prompt,
        payload.proposed_tools,
        selected_case_ids=payload.selected_case_ids,
        min_candidate_score=payload.min_candidate_score,
        min_improvement=payload.min_improvement,
        max_failure_rate=payload.max_failure_rate,
        goal_analysis=payload.goal_analysis,
    )
    # Commit before responding so an immediate dashboard refresh observes the
    # new proposal instead of racing the request-scoped session finalizer.
    await db.commit()
    await db.refresh(item)
    return row(item)


@router.post("/evolution/{proposal_id}/evaluate")
async def evaluate_evolution(
    proposal_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(EvolutionProposal, proposal_id)
    if not item:
        raise not_found("进化提案")
    try:
        return row(await evolution_service.evaluate(db, item))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/evolution/{proposal_id}/evaluate/stream")
async def stream_evolution_evaluation(proposal_id: str) -> StreamingResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_evaluation() -> None:
            try:
                async with session_scope() as db:
                    item = await db.get(EvolutionProposal, proposal_id)
                    if not item:
                        raise LookupError("进化提案不存在")

                    async def publish(event: dict[str, Any]) -> None:
                        await queue.put({"type": "step", "step": event})

                    result = await evolution_service.evaluate(db, item, on_event=publish)
                    await queue.put({"type": "evolution_result", "proposal": row(result)})
            except Exception as exc:
                message = str(exc).strip() or f"{type(exc).__name__}：评测执行异常"
                await queue.put({"type": "error", "message": message})
            finally:
                await queue.put({"type": "done"})

        yield f"data: {dumps({'type': 'step', 'step': {'type': 'stream_connected'}})}\n\n"
        task = asyncio.create_task(run_evaluation())
        active_evolution_tasks.add(task)
        task.add_done_callback(active_evolution_tasks.discard)
        waiting_seconds = 0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=2)
                waiting_seconds = 0
            except TimeoutError:
                waiting_seconds += 2
                event = {
                    "type": "step",
                    "step": {
                        "type": "evaluation_waiting",
                        "elapsed_seconds": waiting_seconds,
                    },
                }
            yield f"data: {dumps(event)}\n\n"
            if event["type"] == "done":
                break
        await task

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/evolution/{proposal_id}/decide")
async def decide_evolution(
    proposal_id: str,
    payload: EvolutionDecision,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await db.get(EvolutionProposal, proposal_id)
    if not item:
        raise not_found("进化提案")
    try:
        result = await evolution_service.decide(
            db,
            item,
            payload.approved,
            payload.decided_by,
            override_gate=payload.override_gate,
            note=payload.note,
        )
        await db.commit()
        await db.refresh(result)
        return row(result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/audit")
async def list_audit(limit: int = 100, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    items = (
        await db.scalars(
            select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit, 500))
        )
    ).all()
    return [row(item) for item in items]
