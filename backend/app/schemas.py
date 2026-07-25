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
    group_id: str | None = None
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
    group_id: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[str] | None = None
    skills: list[str] | None = None
    knowledge_bases: list[str] | None = None
    permissions: dict[str, Any] | None = None
    status: str | None = None


class AgentGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    color: str = Field(default="#1769c2", pattern=r"^#[0-9a-fA-F]{6}$")
    sort_order: int = Field(default=0, ge=0, le=10000)


class AgentGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    sort_order: int | None = Field(default=None, ge=0, le=10000)


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
    security_profile: Literal[
        "default",
        "read_only",
        "workspace_ask",
        "workspace_auto",
        "custom_ask",
        "custom_auto",
        "unrestricted_ask",
        "unrestricted_auto",
    ] = "default"


class UserRegister(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_\-\u4e00-\u9fff]+$",
    )
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_color: str | None = Field(
        default=None, pattern=r"^#[0-9a-fA-F]{6}$"
    )
    memory_enabled: bool | None = None


class UserReplyStyleUpdate(BaseModel):
    style_id: Literal[
        "balanced",
        "concise",
        "professional",
        "friendly",
        "academic",
        "creative",
        "teacher",
        "detailed",
        "custom",
    ]
    custom_style: str = Field(default="", max_length=1200)


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


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    discipline: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)


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


class KnowledgeDocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source: str | None = Field(default=None, max_length=4000)
    content: str | None = Field(default=None, min_length=1)


class KnowledgeSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    uri: str | None = Field(default=None, max_length=4096)
    config: dict[str, Any] | None = None


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
    security_profile: Literal[
        "default",
        "read_only",
        "workspace_ask",
        "workspace_auto",
        "custom_ask",
        "custom_auto",
        "unrestricted_ask",
        "unrestricted_auto",
    ] = "default"


class RuntimeSecurityConfigUpdate(BaseModel):
    filesystem_mode: Literal["workspace", "custom", "unrestricted"] = "workspace"
    workspace_roots: list[str] = Field(default_factory=list, max_length=20)
    command_mode: Literal["risk_based", "always_ask", "auto", "deny"] = "risk_based"
    block_critical_commands: bool = True


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
    reason: str = Field(min_length=3, max_length=2000)
    proposed_prompt: str = Field(default="", max_length=30_000)
    proposed_tools: list[str] | None = None
    selected_case_ids: list[str] = Field(default_factory=list, max_length=100)
    min_candidate_score: float = Field(default=70, ge=0, le=100)
    min_improvement: float = Field(default=0, ge=-100, le=100)
    max_failure_rate: float = Field(default=0.25, ge=0, le=1)
    goal_analysis: dict[str, Any] = Field(default_factory=dict)


class EvolutionGoalAnalyze(BaseModel):
    agent_id: str
    goal: str = Field(min_length=3, max_length=4000)
    include_run_insights: bool = True


class EvaluationCaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    discipline: str = Field(default="通用", max_length=100)
    category: Literal[
        "quality", "reliability", "evidence", "safety", "tool_use", "custom"
    ] = "quality"
    input: str = Field(min_length=3, max_length=20_000)
    expected_keywords: list[str] = Field(default_factory=list)
    requires_citation: bool = False
    weight: float = Field(default=1.0, ge=0.1, le=10)
    enabled: bool = True


class EvaluationCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    discipline: str | None = Field(default=None, max_length=100)
    category: Literal[
        "quality", "reliability", "evidence", "safety", "tool_use", "custom"
    ] | None = None
    input: str | None = Field(default=None, min_length=3, max_length=20_000)
    expected_keywords: list[str] | None = None
    requires_citation: bool | None = None
    weight: float | None = Field(default=None, ge=0.1, le=10)
    enabled: bool | None = None


class EvolutionDecision(BaseModel):
    approved: bool
    decided_by: str = "local-user"
    override_gate: bool = False
    note: str = Field(default="", max_length=2000)


class EvolutionRollback(BaseModel):
    target_agent_id: str
    reason: str = Field(default="用户主动回滚", min_length=2, max_length=1000)
    actor: str = Field(default="local-user", max_length=100)
