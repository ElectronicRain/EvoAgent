from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db, session_scope
from .models import (
    AgentConversation,
    AgentDefinition,
    AgentArtifact,
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
    ModelEndpoint,
    ResearchSourceReview,
    Skill,
    Workflow,
    WorkflowRun,
)
from .schemas import (
    AgentConversationCreate,
    AgentCreate,
    AgentMessageCreate,
    AgentRunRequest,
    AgentUpdate,
    ApprovalDecision,
    ApprovalPolicyCreate,
    ClassroomSpeechRequest,
    EvaluationCaseCreate,
    EvolutionCreate,
    EvolutionDecision,
    ExtensionCreate,
    KnowledgeBaseCreate,
    KnowledgeBaseGroupCreate,
    KnowledgeBaseGroupMembersUpdate,
    KnowledgeBaseGroupUpdate,
    KnowledgeProviderConfigUpdate,
    KnowledgeQueryRequest,
    KnowledgeSearchRequest,
    WebKnowledgeSourceCreate,
    DatabaseKnowledgeSourceCreate,
    APIKnowledgeSourceCreate,
    ModelEndpointCreate,
    ModelEndpointUpdate,
    ResearchSourceReviewCreate,
    TeachingPlanRequest,
    SkillCreate,
    TextDocumentCreate,
    ToolRunRequest,
    WorkflowCreate,
    WorkflowRunRequest,
)
from .services.agents import agent_engine
from .services.common import audit, dumps, loads
from .services.evolution import evolution_service
from .services.extensions import extension_service
from .services.knowledge import knowledge_service
from .services.knowledge_processing import extract_sections
from .services.knowledge_sources import knowledge_source_service
from .services.knowledge_vector import EmbeddingClient, RerankClient, get_knowledge_config
from .services.llm import OpenAICompatibleProvider, get_provider, provider_from_endpoint
from .services.secrets import secret_store
from .services.teaching import teaching_service
from .services.tools import tool_runtime
from .services.workflows import workflow_engine


router = APIRouter(prefix="/api")
active_conversation_tasks: set[asyncio.Task] = set()
active_workflow_tasks: set[asyncio.Task] = set()
active_evolution_tasks: set[asyncio.Task] = set()


def row(model: Any) -> dict[str, Any]:
    return {column.name: getattr(model, column.name) for column in model.__table__.columns}


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
    return {
        "counts": counts,
        "recent_runs": [row(item) for item in recent_runs],
        "runtime": {
            "database": "SQLite",
            "workspace": str(tool_runtime.root),
            "safety": "workspace-isolated",
        },
    }


@router.get("/agents")
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    items = (await db.scalars(select(AgentDefinition).order_by(AgentDefinition.name))).all()
    return [row(item) for item in items]


@router.post("/agents", status_code=201)
async def create_agent(payload: AgentCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if await db.scalar(select(AgentDefinition).where(AgentDefinition.slug == payload.slug)):
        raise HTTPException(status_code=409, detail="Agent slug 已存在")
    endpoint_id = payload.model_endpoint_id or None
    if endpoint_id and not await db.get(ModelEndpoint, endpoint_id):
        raise not_found("模型接口")
    item = AgentDefinition(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        system_prompt=payload.system_prompt,
        provider=payload.provider,
        model_endpoint_id=endpoint_id,
        model=payload.model,
        temperature=payload.temperature,
        tools_json=dumps(payload.tools),
        skills_json=dumps(payload.skills),
        knowledge_bases_json=dumps(payload.knowledge_bases),
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
    json_fields = {
        "tools": "tools_json",
        "skills": "skills_json",
        "knowledge_bases": "knowledge_bases_json",
        "permissions": "permissions_json",
    }
    for key, value in values.items():
        if key in json_fields:
            setattr(item, json_fields[key], dumps(value))
        elif key == "model_endpoint_id":
            endpoint_id = value or None
            if endpoint_id and not await db.get(ModelEndpoint, endpoint_id):
                raise not_found("模型接口")
            setattr(item, key, endpoint_id)
        else:
            setattr(item, key, value)
    await audit(db, "agent.updated", "agent", item.id)
    return row(item)


@router.post("/agents/{agent_id}/run")
async def run_agent(
    agent_id: str, payload: AgentRunRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    result = await agent_engine.run(db, agent_id, payload.input, payload.context)
    return row(result)


@router.get("/agents/{agent_id}/conversations")
async def list_agent_conversations(
    agent_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    items = (
        await db.scalars(
            select(AgentConversation)
            .where(AgentConversation.agent_id == agent_id)
            .order_by(desc(AgentConversation.updated_at))
        )
    ).all()
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
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if not await db.get(AgentDefinition, agent_id):
        raise not_found("Agent")
    item = AgentConversation(agent_id=agent_id, title=payload.title)
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
    runs = {
        item.id: item
        for item in (
            await db.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))
        ).all()
    } if run_ids else {}
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
    return [row(item) for item in items]


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
        await db.get(ModelEndpoint, agent.model_endpoint_id)
        if agent.model_endpoint_id
        else None
    )
    endpoint = bound_endpoint
    if not endpoint:
        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(ModelEndpoint.enabled.is_(True))
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
    result["cloud_tts_available"] = bool(
        endpoint and "siliconflow.cn" in endpoint.base_url.lower()
    )
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
            .where(ModelEndpoint.enabled.is_(True))
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
        f"{base_url}/audio/speech"
        if base_url.endswith("/v1")
        else f"{base_url}/v1/audio/speech"
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


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_conversation_message(
    conversation_id: str, payload: AgentMessageCreate
) -> StreamingResponse:
    async def generate():
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def run_turn() -> None:
            try:
                async with session_scope() as db:
                    conversation = await db.get(AgentConversation, conversation_id)
                    if not conversation:
                        raise LookupError("会话不存在")
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

                    async def publish_step(event: dict[str, Any]) -> None:
                        if event.get("type") == "run_started" and event.get("run_id"):
                            user_message.run_id = str(event["run_id"])
                            await db.commit()
                        await queue.put({"type": "step", "step": event})

                    result = await agent_engine.run(
                        db,
                        conversation.agent_id,
                        payload.content,
                        user_context={"conversation_id": conversation_id},
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
                    await audit(
                        db,
                        "conversation.turn.completed",
                        "agent_conversation",
                        conversation.id,
                        {"run_id": result.id, "status": result.status},
                        success=result.status == "completed",
                    )
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

        yield f'data: {dumps({"type": "step", "step": {"type": "stream_connected"}})}\n\n'
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
        await db.scalars(select(AgentRun).order_by(desc(AgentRun.created_at)).limit(min(limit, 200)))
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


@router.post("/workflows", status_code=201)
async def create_workflow(
    payload: WorkflowCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = Workflow(
        name=payload.name,
        description=payload.description,
        definition_json=dumps(payload.definition),
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
    item.name = payload.name
    item.description = payload.description
    item.definition_json = dumps(payload.definition)
    item.version += 1
    await audit(db, "workflow.updated", "workflow", item.id)
    return row(item)


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str, payload: WorkflowRunRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        result = await workflow_engine.run(db, workflow_id, payload.input)
    except LookupError as exc:
        raise not_found("工作流") from exc
    return row(result)


@router.post("/workflows/{workflow_id}/run/stream")
async def stream_workflow_run(
    workflow_id: str, payload: WorkflowRunRequest
) -> StreamingResponse:
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
                    )
                    await queue.put({"type": "workflow_result", "run": row(result)})
            except Exception as exc:
                message = str(exc).strip() or f"{type(exc).__name__}：工作流执行异常"
                await queue.put({"type": "error", "message": message})
            finally:
                await queue.put({"type": "done"})

        yield f'data: {dumps({"type": "step", "step": {"type": "stream_connected"}})}\n\n'
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


@router.get("/workflow-runs")
async def list_workflow_runs(
    limit: int = 50, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = (
        await db.scalars(
            select(WorkflowRun).order_by(desc(WorkflowRun.created_at)).limit(min(limit, 200))
        )
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
        await db.scalars(select(ApprovalPolicy).order_by(ApprovalPolicy.priority, ApprovalPolicy.name))
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
    if "api_key" in values:
        item.api_key_ciphertext = secret_store.encrypt(values.pop("api_key") or "")
    if "headers" in values:
        item.headers_json = dumps(values.pop("headers"))
    if "request_options" in values:
        item.request_options_json = dumps(values.pop("request_options"))
    for key, value in values.items():
        setattr(item, key, value.rstrip("/") if key == "base_url" else value)
    await audit(db, "model_endpoint.updated", "model_endpoint", item.id)
    return endpoint_row(item)


@router.post("/model-endpoints/{endpoint_id}/test")
async def test_model_endpoint(
    endpoint_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = await db.get(ModelEndpoint, endpoint_id)
    if not item:
        raise not_found("模型接口")
    try:
        provider = OpenAICompatibleProvider(
            item.base_url,
            secret_store.decrypt(item.api_key_ciphertext),
            headers=loads(item.headers_json, {}),
            request_options=loads(item.request_options_json, {}),
            timeout_seconds=item.timeout_seconds,
        )
        response = await provider.chat(
            [{"role": "user", "content": "只回复 OK"}],
            model=item.default_model,
            temperature=0,
        )
        item.health = "healthy"
        result = {"status": "healthy", "response": response.content[:200]}
    except Exception as exc:
        item.health = "unhealthy"
        result = {"status": "unhealthy", "error": str(exc)}
    await audit(db, "model_endpoint.tested", "model_endpoint", item.id, result)
    return result


@router.post("/tools/run")
async def run_tool(payload: ToolRunRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        return await tool_runtime.execute(
            db,
            payload.tool,
            payload.arguments,
            run_id=payload.run_id,
            policy_id=payload.policy_id,
            permission_mode=payload.permission_mode,
        )
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/approvals")
async def list_approvals(
    status: str | None = None, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    statement = select(Approval).order_by(desc(Approval.created_at))
    if status:
        statement = statement.where(Approval.status == status)
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
        result = await tool_runtime.execute(
            db,
            item.action_type.split(":", 1)[1],
            loads(item.payload_json, {}),
            run_id=item.run_id,
            permission_mode="auto",
            preapproved=True,
        )
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


async def _knowledge_group_row(
    db: AsyncSession, group: KnowledgeBaseGroup
) -> dict[str, Any]:
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
    groups = (
        await db.scalars(select(KnowledgeBaseGroup).order_by(KnowledgeBaseGroup.name))
    ).all()
    return [await _knowledge_group_row(db, group) for group in groups]


@router.post("/knowledge-groups", status_code=201)
async def create_knowledge_group(
    payload: KnowledgeBaseGroupCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if await db.scalar(select(KnowledgeBaseGroup.id).where(KnowledgeBaseGroup.name == payload.name)):
        raise HTTPException(status_code=409, detail="知识库分组名称已存在")
    base_ids = list(dict.fromkeys(payload.knowledge_base_ids))
    if base_ids:
        existing = set(
            (
                await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(base_ids)))
            ).all()
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
    return await _knowledge_group_row(db, group)


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
    return await _knowledge_group_row(db, group)


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
    existing = set(
        (
            await db.scalars(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(base_ids)))
        ).all()
    ) if base_ids else set()
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
    return await _knowledge_group_row(db, group)


@router.delete("/knowledge-groups/{group_id}", status_code=204)
async def delete_knowledge_group(
    group_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    group = await db.get(KnowledgeBaseGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="知识库分组不存在")
    await db.delete(group)
    await audit(db, "knowledge.group_deleted", "knowledge_base_group", group_id)
    return Response(status_code=204)


@router.post("/knowledge-bases", status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    item = KnowledgeBase(**payload.model_dump())
    db.add(item)
    await db.flush()
    await audit(db, "knowledge.created", "knowledge_base", item.id)
    return row(item)


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
            select(KnowledgeSource.source_type, KnowledgeSource.status, func.count(KnowledgeSource.id))
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
    total = int(
        await db.scalar(select(func.count(KnowledgeChunk.id)).where(*filters)) or 0
    )
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
    return row(item)


@router.post("/knowledge-bases/{knowledge_base_id}/documents/upload", status_code=201)
async def upload_document(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件不能超过 25MB")
    try:
        filename = file.filename or "document.txt"
        sections, mime = extract_sections(filename, data)
        source = await knowledge_source_service.create(
            db,
            knowledge_base_id,
            name=filename,
            source_type="file",
            uri=filename,
            config={"filename": filename},
        )
        item, result = await knowledge_service.add_sections(
            db,
            knowledge_base_id,
            title=filename,
            sections=sections,
            source=f"本地文件：{filename}",
            mime_type=mime,
            source_id=source.id,
            metadata={"filename": filename, "source_type": "file"},
        )
        source.status = "ready"
        source.last_synced_at = datetime.now(timezone.utc)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=400, detail="文档清洗后没有可用内容")
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
    return knowledge_config_row(config)


@router.post("/knowledge/config/test")
async def test_knowledge_provider_config(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    config = await get_knowledge_config(db)
    try:
        embedder = EmbeddingClient(config)
        vectors = await embedder.embed(["EvoAgent 知识库连接测试"])
        reranked = await RerankClient(config).rerank(
            "网格质量", ["天气预报", "网格质量评价"], 1
        )
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
    return result


@router.get("/knowledge-bases/{knowledge_base_id}/sources")
async def list_knowledge_sources(
    knowledge_base_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = await knowledge_source_service.list_for_base(db, knowledge_base_id)
    return [knowledge_source_service.public_row(item) for item in items]


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
        return row(job)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        source = await db.get(KnowledgeSource, source_id)
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
        return await knowledge_service.reindex(db, knowledge_base_id)
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
        result = await tool_runtime.execute(
            db,
            name,
            dict(params.get("arguments") or {}),
            permission_mode="auto",
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
async def list_skills(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in (await db.scalars(select(Skill).order_by(Skill.name))).all()]


@router.post("/skills", status_code=201)
async def create_skill(payload: SkillCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = Skill(**payload.model_dump())
    db.add(item)
    await db.flush()
    await audit(db, "skill.created", "skill", item.id)
    return row(item)


@router.post("/skills/sync")
async def sync_skills(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return [row(item) for item in await extension_service.sync_skills(db)]


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
    return await extension_service.call_mcp_tool(item, tool_name, arguments)


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
        input_text=payload.input,
        expected_keywords_json=dumps(payload.expected_keywords),
        requires_citation=payload.requires_citation,
    )
    db.add(item)
    await db.flush()
    return row(item)


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
    )
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

        yield f'data: {dumps({"type": "step", "step": {"type": "stream_connected"}})}\n\n'
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
        return row(
            await evolution_service.decide(db, item, payload.approved, payload.decided_by)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/audit")
async def list_audit(
    limit: int = 100, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    items = (
        await db.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(min(limit, 500)))
    ).all()
    return [row(item) for item in items]
