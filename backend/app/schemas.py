from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,99}$")
    description: str = ""
    system_prompt: str = Field(min_length=10)
    provider: str = "demo"
    model_endpoint_id: str | None = None
    model: str = "demo-model"
    temperature: float = Field(default=0.3, ge=0, le=2)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    is_template: bool = False


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    provider: str | None = None
    model_endpoint_id: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[str] | None = None
    skills: list[str] | None = None
    knowledge_bases: list[str] | None = None
    permissions: dict[str, Any] | None = None
    status: str | None = None


class AgentRead(ORMModel):
    id: str
    lineage_id: str
    parent_id: str | None
    name: str
    slug: str
    description: str
    system_prompt: str
    provider: str
    model_endpoint_id: str | None
    model: str
    temperature: float
    tools_json: str
    skills_json: str
    knowledge_bases_json: str
    permissions_json: str
    version: int
    status: str
    is_template: bool


class AgentRunRequest(BaseModel):
    input: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=160)


class AgentMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class ResearchSourceReviewCreate(BaseModel):
    run_id: str | None = None
    url: str = Field(min_length=8, max_length=4000)
    title: str = Field(default="", max_length=500)
    decision: Literal["confirmed", "excluded"]
    credibility: dict[str, Any] = Field(default_factory=dict)


class TeachingPlanRequest(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=100)
    section_indices: list[int] = Field(default_factory=list, max_length=200)


class ClassroomSpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    voice: Literal["alex", "benjamin", "charles", "david", "anna", "bella", "claire", "diana"] = "claire"
    style: Literal["natural", "lively", "rigorous"] = "natural"


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    definition: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseCreate(BaseModel):
    name: str
    discipline: str = "通用"
    description: str = ""


class KnowledgeBaseGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    color: str = Field(default="#1769c2", pattern=r"^#[0-9A-Fa-f]{6}$")
    knowledge_base_ids: list[str] = Field(default_factory=list)


class KnowledgeBaseGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class KnowledgeBaseGroupMembersUpdate(BaseModel):
    knowledge_base_ids: list[str] = Field(default_factory=list)


class TextDocumentCreate(BaseModel):
    title: str
    content: str = Field(min_length=1)
    source: str = "用户录入"


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    knowledge_group_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=5, ge=1, le=20)


class KnowledgeQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    knowledge_group_ids: list[str] = Field(default_factory=list)
    top_k: int | None = Field(default=None, ge=1, le=20)
    candidate_k: int | None = Field(default=None, ge=5, le=100)
    generate_answer: bool = True


class WebKnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=8, max_length=2048)
    max_pages: int = Field(default=1, ge=1, le=20)
    same_domain: bool = True
    sync_now: bool = True


class DatabaseKnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    connection_url: str = Field(min_length=5, max_length=4096)
    query: str = Field(min_length=6, max_length=20_000)
    params: dict[str, Any] = Field(default_factory=dict)
    row_limit: int = Field(default=5000, ge=1, le=20_000)
    title: str = ""
    sync_now: bool = True


class APIKnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=8, max_length=2048)
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    body: Any | None = None
    response_path: str = ""
    title: str = ""
    sync_now: bool = True


class KnowledgeProviderConfigUpdate(BaseModel):
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    rerank_base_url: str | None = None
    rerank_model: str | None = None
    api_key: str | None = None
    llm_endpoint_id: str | None = None
    embedding_batch_size: int | None = Field(default=None, ge=1, le=64)
    candidate_k: int | None = Field(default=None, ge=5, le=100)
    top_k: int | None = Field(default=None, ge=1, le=20)
    context_char_budget: int | None = Field(default=None, ge=1000, le=100_000)


class SkillCreate(BaseModel):
    name: str
    description: str = ""
    instructions: str = Field(min_length=10)
    version: str = "1.0.0"


class ExtensionCreate(BaseModel):
    name: str
    kind: Literal["mcp", "plugin"]
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)


class ToolRunRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    permission_mode: Literal["auto", "ask", "deny"] = "ask"
    policy_id: str | None = None


class ApprovalPolicyCreate(BaseModel):
    name: str
    description: str = ""
    priority: int = Field(default=100, ge=0, le=10_000)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    is_default: bool = False


class ModelEndpointCreate(BaseModel):
    name: str
    provider_type: Literal["openai-compatible", "spark-compatible", "custom"] = (
        "openai-compatible"
    )
    base_url: str
    api_key: str = ""
    default_model: str
    headers: dict[str, str] = Field(default_factory=dict)
    request_options: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=90, ge=5, le=300)
    enabled: bool = True


class ModelEndpointUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    default_model: str | None = None
    headers: dict[str, str] | None = None
    request_options: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    decided_by: str = "local-user"


class EvolutionCreate(BaseModel):
    agent_id: str
    reason: str
    proposed_prompt: str = Field(min_length=10)
    proposed_tools: list[str] | None = None


class EvaluationCaseCreate(BaseModel):
    name: str
    discipline: str = "通用"
    input: str
    expected_keywords: list[str] = Field(default_factory=list)
    requires_citation: bool = False


class EvolutionDecision(BaseModel):
    approved: bool
    decided_by: str = "local-user"
