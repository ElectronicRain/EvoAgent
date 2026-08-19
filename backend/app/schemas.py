from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


DEFAULT_AGENT_PROMPT_TEMPLATE = """你是一个以证据为中心的智能助手。
只依据“检索证据”回答知识性问题；证据不足时明确说明，不得编造。
关键结论必须使用 [资料 N] 引用。若用户要求全部要点或编号列表，必须保持原顺序完整列出。

【对话历史】
{history}

【知识库检索结果】
{knowledge}

【可用引用】
{citations}

【用户问题】
{question}"""


class AgentRAGConfig(BaseModel):
    enabled: bool = True
    knowledge_group_ids: list[str] = Field(default_factory=list)
    similarity_threshold: float = Field(default=0.0, ge=0, le=1)
    dense_weight: float = Field(default=0.65, ge=0, le=1)
    lexical_weight: float = Field(default=0.35, ge=0, le=1)
    candidate_k: int = Field(default=30, ge=5, le=100)
    rerank_k: int = Field(default=12, ge=1, le=50)
    top_k: int = Field(default=6, ge=1, le=20)
    context_char_budget: int = Field(default=12000, ge=1000, le=100_000)
    query_rewrite: bool = True
    multi_turn: bool = True
    max_history_messages: int = Field(default=8, ge=0, le=20)
    cross_language: bool = False
    knowledge_graph: bool = False
    parent_expansion: bool = True
    complete_list_expansion: bool = True
    rerank_model: str = Field(default="", max_length=180)

    @model_validator(mode="after")
    def validate_weights(self) -> "AgentRAGConfig":
        if self.dense_weight + self.lexical_weight <= 0:
            raise ValueError("向量与全文检索权重不能同时为 0")
        if self.rerank_k < self.top_k:
            self.rerank_k = self.top_k
        if self.candidate_k < self.rerank_k:
            self.candidate_k = self.rerank_k
        return self


class AgentGenerationConfig(BaseModel):
    opening_message: str = Field(default="", max_length=2000)
    suggested_questions: list[str] = Field(default_factory=list, max_length=8)
    prompt_template: str = Field(default=DEFAULT_AGENT_PROMPT_TEMPLATE, min_length=20)
    top_p: float = Field(default=0.9, gt=0, le=1)
    max_output_tokens: int = Field(default=2048, ge=128, le=32768)
    grounded_refusal: bool = True
    citation_required: bool = True
    verify_answer: bool = True
    repair_retry: bool = True
    custom_variables: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_template(self) -> "AgentGenerationConfig":
        required = {"{question}", "{knowledge}"}
        missing = sorted(item for item in required if item not in self.prompt_template)
        if missing:
            raise ValueError(f"提示词模板缺少保留变量：{', '.join(missing)}")
        reserved = {"question", "knowledge", "history", "citations"}
        collision = sorted(reserved.intersection(self.custom_variables))
        if collision:
            raise ValueError(f"自定义变量不能覆盖保留变量：{', '.join(collision)}")
        self.suggested_questions = [
            value.strip() for value in self.suggested_questions if value.strip()
        ][:8]
        return self


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,99}$")
    description: str = ""
    system_prompt: str = Field(min_length=10)
    provider: str = "demo"
    model_endpoint_id: str | None = None
    image_model_endpoint_id: str | None = None
    group_id: str | None = None
    model: str = "demo-model"
    temperature: float = Field(default=0.3, ge=0, le=2)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    rag_config: AgentRAGConfig = Field(default_factory=AgentRAGConfig)
    generation_config: AgentGenerationConfig = Field(default_factory=AgentGenerationConfig)
    permissions: dict[str, Any] = Field(default_factory=dict)
    is_template: bool = False


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    provider: str | None = None
    model_endpoint_id: str | None = None
    image_model_endpoint_id: str | None = None
    group_id: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[str] | None = None
    skills: list[str] | None = None
    knowledge_bases: list[str] | None = None
    rag_config: AgentRAGConfig | None = None
    generation_config: AgentGenerationConfig | None = None
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
    image_model_endpoint_id: str | None
    model: str
    temperature: float
    tools_json: str
    skills_json: str
    knowledge_bases_json: str
    rag_config_json: str
    generation_config_json: str
    permissions_json: str
    version: int
    status: str
    is_template: bool


class AgentRunRequest(BaseModel):
    input: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRAGPreviewRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class AgentRAGEvaluationRequest(BaseModel):
    case_ids: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=20, ge=1, le=50)


class AgentConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=160)


class AgentMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    skill_ids: list[str] = Field(default_factory=list, max_length=20)
    security_profile: Literal[
        "default",
        "read_only",
        "workspace",
        "custom",
        "unrestricted",
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
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
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


class TelemetryEventCreate(BaseModel):
    event_type: str = Field(min_length=2, max_length=100)
    module: str = Field(default="frontend", max_length=60)
    resource_type: str = Field(default="", max_length=60)
    resource_id: str | None = Field(default=None, max_length=100)
    success: bool = True
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    detail: dict[str, Any] = Field(default_factory=dict)


class TelemetryHubDeviceRegister(BaseModel):
    installation_id: str = Field(min_length=24, max_length=64)
    device_name: str = Field(default="", max_length=160)
    platform: str = Field(default="", max_length=120)
    app_version: str = Field(default="", max_length=30)


class TelemetryHubEvent(BaseModel):
    id: str = Field(min_length=16, max_length=64)
    installation_id: str = Field(min_length=24, max_length=64)
    user_id: str | None = Field(default=None, max_length=36)
    username: str = Field(default="anonymous", max_length=80)
    event_type: str = Field(min_length=2, max_length=100)
    module: str = Field(default="system", max_length=60)
    resource_type: str = Field(default="", max_length=60)
    resource_id: str | None = Field(default=None, max_length=100)
    success: bool = True
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    detail: dict[str, Any] = Field(default_factory=dict)
    error_fingerprint: str = Field(default="", max_length=64)
    client_version: str = Field(default="", max_length=30)
    occurred_at: datetime


class TelemetryHubBatch(BaseModel):
    events: list[TelemetryHubEvent] = Field(default_factory=list, max_length=1000)


class AdminUserUpdate(BaseModel):
    status: Literal["active", "disabled"]
    note: str = Field(default="", max_length=500)


class ResearchSourceReviewCreate(BaseModel):
    run_id: str | None = None
    url: str = Field(min_length=8, max_length=4000)
    title: str = Field(default="", max_length=500)
    decision: Literal["confirmed", "excluded"]
    credibility: dict[str, Any] = Field(default_factory=dict)


class ResearchVerificationComplete(BaseModel):
    verification_id: str = Field(min_length=8, max_length=100)
    approved: bool = True
    url: str = Field(min_length=8, max_length=4000)
    cookies: list[dict[str, Any]] = Field(default_factory=list, max_length=80)


class TeachingPlanRequest(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=100)
    section_indices: list[int] = Field(default_factory=list, max_length=200)


class ClassroomSpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    voice: Literal["alex", "benjamin", "charles", "david", "anna", "bella", "claire", "diana"] = (
        "claire"
    )
    style: Literal["natural", "lively", "rigorous"] = "natural"


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = ""
    definition: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})


class WorkflowClarificationRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    workflow_name: str = Field(default="", max_length=120)
    workflow_description: str = Field(default="", max_length=4000)
    definition: dict[str, Any] = Field(default_factory=dict)
    answers: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    phase: Literal["run", "orchestration"] = "run"


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    loop_enabled: bool | None = None
    loop_count: int | None = Field(default=None, ge=1, le=50)
    artifact_enabled: bool | None = None
    security_profile: Literal[
        "default",
        "read_only",
        "workspace",
        "custom",
        "unrestricted",
        "workspace_ask",
        "workspace_auto",
        "custom_ask",
        "custom_auto",
        "unrestricted_ask",
        "unrestricted_auto",
    ] = "default"
    permission_mode: Literal["inherit", "ask", "auto", "deny"] = "inherit"
    approval_policy_id: str | None = None


class WorkflowRunControlRequest(BaseModel):
    action: Literal["pause", "resume", "interrupt", "guide"]
    message: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_guidance(self) -> "WorkflowRunControlRequest":
        if self.action == "guide" and not self.message.strip():
            raise ValueError("引导内容不能为空")
        return self


class WorkflowExpertChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=8000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    current_definition: dict[str, Any] | None = None
    current_agent_drafts: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    workflow_name: str = Field(default="", max_length=120)
    workflow_description: str = Field(default="", max_length=4000)


class WorkflowExpertMaterializeRequest(BaseModel):
    proposal: dict[str, Any]


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
    modality: Literal["chat", "image"] = "chat"
    provider_type: Literal["openai-compatible", "spark-compatible", "custom"] = "openai-compatible"
    base_url: str
    api_key: str = ""
    default_model: str
    headers: dict[str, str] = Field(default_factory=dict)
    request_options: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=90, ge=5, le=300)
    enabled: bool = True


class ModelEndpointUpdate(BaseModel):
    name: str | None = None
    modality: Literal["chat", "image"] | None = None
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
    category: Literal["quality", "reliability", "evidence", "safety", "tool_use", "custom"] = (
        "quality"
    )
    input: str = Field(min_length=3, max_length=20_000)
    expected_keywords: list[str] = Field(default_factory=list)
    requires_citation: bool = False
    weight: float = Field(default=1.0, ge=0.1, le=10)
    enabled: bool = True


class EvaluationCaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    discipline: str | None = Field(default=None, max_length=100)
    category: (
        Literal["quality", "reliability", "evidence", "safety", "tool_use", "custom"] | None
    ) = None
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


class ResearchProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    discipline: str = Field(default="计算机科学", max_length=100)
    description: str = Field(default="", max_length=20_000)
    research_question: str = Field(default="", max_length=20_000)
    expected_outcome: str = Field(default="论文", max_length=1000)
    citation_style: Literal["GB/T 7714", "APA", "IEEE", "Chicago"] = "GB/T 7714"
    language: Literal["zh-CN", "en-US", "bilingual"] = "zh-CN"


class ResearchProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    discipline: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=20_000)
    research_question: str | None = Field(default=None, max_length=20_000)
    expected_outcome: str | None = Field(default=None, max_length=1000)
    citation_style: Literal["GB/T 7714", "APA", "IEEE", "Chicago"] | None = None
    language: Literal["zh-CN", "en-US", "bilingual"] | None = None
    stage: Literal["literature", "idea", "experiment", "writing", "review"] | None = None
    status: Literal["active", "archived"] | None = None
    settings: dict[str, Any] | None = None


class ResearchMemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    role: Literal["manager", "editor", "reviewer", "viewer"] = "editor"


class ResearchInviteCreate(BaseModel):
    role: Literal["manager", "editor", "reviewer", "viewer"] = "editor"
    expires_hours: int = Field(default=72, ge=1, le=720)
    max_uses: int = Field(default=20, ge=1, le=200)


class ResearchInviteJoin(BaseModel):
    code: str = Field(min_length=8, max_length=120)


class ResearchResourceCreate(BaseModel):
    resource_type: Literal[
        "agent",
        "knowledge_base",
        "knowledge_group",
        "workflow",
        "conversation",
        "artifact",
        "skill",
    ]
    resource_id: str = Field(min_length=1, max_length=100)
    label: str = Field(default="", max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchLiteratureCreate(BaseModel):
    title: str = Field(min_length=2, max_length=2000)
    authors: str = Field(default="", max_length=2000)
    year: int | None = Field(default=None, ge=1800, le=2200)
    doi: str = Field(default="", max_length=300)
    url: str = Field(default="", max_length=4000)
    source: str = Field(default="手动录入", max_length=120)
    abstract: str = Field(default="", max_length=50_000)
    status: Literal["pending", "included", "excluded", "priority", "disputed"] = "pending"
    credibility: int = Field(default=50, ge=0, le=100)
    tags: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=20_000)


class ResearchLiteratureSearch(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    target_count: int = Field(default=12, ge=3, le=80)
    year_from: int | None = Field(default=None, ge=1800, le=2200)
    year_to: int | None = Field(default=None, ge=1800, le=2200)


class ResearchFrontierTrack(BaseModel):
    query: str = Field(default="", max_length=4000)
    recent_years: int = Field(default=3, ge=1, le=15)
    target_count: int = Field(default=20, ge=3, le=80)
    refresh: bool = True


class ResearchFigureGenerate(BaseModel):
    dataset_id: str
    argument: str = Field(min_length=2, max_length=4000)
    chart_type: Literal[
        "auto", "strip", "scatter", "correlation", "histogram", "bar",
        "pie", "3d", "dual_y", "jet",
    ] = "auto"
    x: str = Field(default="", max_length=240)
    y: str = Field(default="", max_length=240)
    group: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=500)
    journal: Literal[
        "general", "nature", "science", "ieee", "elsevier", "pnas", "chinese_core"
    ] = "general"


class ResearchIdeaCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    problem: str = Field(default="", max_length=20_000)
    hypothesis: str = Field(default="", max_length=20_000)
    novelty: str = Field(default="", max_length=20_000)
    method: str = Field(default="", max_length=20_000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    scores: dict[str, float] = Field(default_factory=dict)
    status: Literal["draft", "exploring", "validation", "adopted", "rejected"] = "exploring"


class ResearchIdeaUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    problem: str | None = Field(default=None, max_length=20_000)
    hypothesis: str | None = Field(default=None, max_length=20_000)
    novelty: str | None = Field(default=None, max_length=20_000)
    method: str | None = Field(default=None, max_length=20_000)
    evidence: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    scores: dict[str, float] | None = None
    status: Literal["draft", "exploring", "validation", "adopted", "rejected"] | None = None


class ResearchIdeaChat(BaseModel):
    message: str = Field(min_length=2, max_length=12_000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=40)
    agent_id: str | None = None


class ResearchMemoryCreate(BaseModel):
    category: Literal[
        "background",
        "concept",
        "evidence",
        "decision",
        "hypothesis",
        "method",
        "constraint",
        "failure",
        "writing",
        "todo",
    ] = "decision"
    content: str = Field(min_length=2, max_length=20_000)
    source_type: str = Field(default="user", max_length=50)
    source_id: str | None = Field(default=None, max_length=36)
    confidence: float = Field(default=1.0, ge=0, le=1)
    locked: bool = False


class ResearchSkillDraft(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    memory_ids: list[str] = Field(default_factory=list, max_length=100)


class ResearchExperimentCreate(BaseModel):
    idea_id: str | None = None
    title: str = Field(min_length=2, max_length=240)
    objective: str = Field(default="", max_length=20_000)
    hypothesis: str = Field(default="", max_length=20_000)
    design: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    status: Literal["planned", "running", "completed", "failed"] = "planned"


class ResearchExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    objective: str | None = Field(default=None, max_length=20_000)
    hypothesis: str | None = Field(default=None, max_length=20_000)
    design: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    status: Literal["planned", "running", "completed", "failed"] | None = None


class ResearchManuscriptCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(default="", max_length=1_000_000)
    bibliography: str = Field(default="", max_length=500_000)


class ResearchManuscriptFile(BaseModel):
    content: str = Field(default="", max_length=8_000_000)
    encoding: Literal["utf8", "base64"] = "utf8"
    mime: str = Field(default="text/plain", max_length=200)
    size: int = Field(default=0, ge=0, le=25_000_000)


class ResearchManuscriptUpdate(BaseModel):
    content: str = Field(max_length=1_000_000)
    bibliography: str = Field(default="", max_length=500_000)
    main_file: str = Field(default="main.tex", min_length=1, max_length=500)
    files: dict[str, ResearchManuscriptFile] | None = None
    base_version: int = Field(ge=1)
    change_summary: str = Field(default="", max_length=2000)


class ResearchManuscriptAssist(BaseModel):
    task: Literal[
        "outline",
        "polish",
        "logic",
        "citation_check",
        "academic_style",
        "translate",
        "response_letter",
    ]
    selection: str = Field(default="", max_length=120_000)
    instruction: str = Field(default="", max_length=4000)
    agent_id: str | None = None


class ResearchManuscriptRestore(BaseModel):
    version: int = Field(ge=1)
    base_version: int = Field(ge=1)


class ResearchCommentCreate(BaseModel):
    manuscript_id: str | None = None
    parent_id: str | None = None
    file_path: str = Field(default="main.tex", min_length=1, max_length=500)
    anchored_version: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    quote: str = Field(default="", max_length=20_000)
    content: str = Field(min_length=1, max_length=20_000)


class ResearchCommentUpdate(BaseModel):
    status: Literal["open", "resolved"]


class ResearchReviewCreate(BaseModel):
    manuscript_id: str
    roles: list[Literal["domain", "method", "experiment", "statistics", "writing", "strict"]] = (
        Field(default_factory=lambda: ["domain", "method", "writing"], max_length=6)
    )
    venue: str = Field(default="通用学术期刊/会议", max_length=240)
    rigor: Literal["standard", "strict", "top_venue"] = "strict"
    focus: str = Field(default="", max_length=4000)
    agent_ids: dict[str, str] = Field(default_factory=dict)


class ResearchReviewItemUpdate(BaseModel):
    status: Literal["open", "accepted", "resolved", "rejected"] | None = None
    response: str | None = Field(default=None, max_length=20_000)


class ResearchPresenceUpdate(BaseModel):
    page: str = Field(default="overview", max_length=80)
    cursor: dict[str, Any] = Field(default_factory=dict)


class LearningProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    project_type: Literal["course", "exam", "skill", "topic", "project"] = "course"
    discipline: str = Field(default="计算机科学", max_length=100)
    description: str = Field(default="", max_length=20_000)
    target: str = Field(default="", max_length=20_000)
    current_level: Literal["beginner", "foundation", "intermediate", "advanced"] = "beginner"
    target_level: Literal["foundation", "intermediate", "proficient", "advanced"] = "proficient"
    weekly_hours: float = Field(default=6, ge=1, le=80)
    deadline: datetime | None = None
    track: str = Field(default="计算机基础", max_length=100)


class LearningProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    project_type: Literal["course", "exam", "skill", "topic", "project"] | None = None
    description: str | None = Field(default=None, max_length=20_000)
    target: str | None = Field(default=None, max_length=20_000)
    current_level: Literal["beginner", "foundation", "intermediate", "advanced"] | None = None
    target_level: Literal["foundation", "intermediate", "proficient", "advanced"] | None = None
    weekly_hours: float | None = Field(default=None, ge=1, le=80)
    deadline: datetime | None = None
    stage: Literal["planning", "learning", "practice", "review", "completed"] | None = None
    status: Literal["active", "archived"] | None = None
    settings: dict[str, Any] | None = None


class LearningBindingsUpdate(BaseModel):
    agents: dict[str, str] = Field(default_factory=dict)
    workflows: dict[str, str] = Field(default_factory=dict)
    knowledge_base_ids: list[str] | None = None
    knowledge_group_id: str | None = None


class LearningPlanGenerate(BaseModel):
    regenerate: bool = False
    start_at: datetime | None = None
    focus: list[str] = Field(default_factory=list, max_length=30)


class LearningPathReplan(BaseModel):
    regenerate_plan: bool = True
    start_at: datetime | None = None
    focus: list[str] = Field(default_factory=list, max_length=30)


class LearningCompanionRequest(BaseModel):
    minutes: int = Field(default=45, ge=10, le=180)
    mood: Literal["focused", "normal", "tired", "stressed"] = "normal"
    goal: str = Field(default="", max_length=2000)


class LearningDirectionRegenerate(BaseModel):
    track: str | None = Field(default=None, max_length=100)
    keep_memories: bool = True


class LearningTaskCreate(BaseModel):
    knowledge_node_id: str | None = None
    module: Literal["learn", "practice", "review", "assessment", "project"] = "learn"
    title: str = Field(min_length=2, max_length=240)
    description: str = Field(default="", max_length=10_000)
    scheduled_for: datetime | None = None
    duration_minutes: int = Field(default=45, ge=5, le=480)
    priority: int = Field(default=3, ge=1, le=5)


class LearningTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    description: str | None = Field(default=None, max_length=10_000)
    scheduled_for: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    priority: int | None = Field(default=None, ge=1, le=5)
    progress: int | None = Field(default=None, ge=0, le=100)
    status: Literal["pending", "in_progress", "completed", "skipped"] | None = None


class LearningTutorChat(BaseModel):
    message: str = Field(min_length=1, max_length=12_000)
    mode: Literal["socratic", "explain", "examiner", "debug", "feynman", "sprint"] = "socratic"
    knowledge_node_id: str | None = None
    agent_id: str | None = None


class TeachingSessionCreate(BaseModel):
    document_id: str
    agent_id: str | None = None
    pace: Literal["slow", "standard", "fast"] = "standard"
    depth: Literal["introductory", "course", "exam", "deep"] = "course"
    duration_minutes: int = Field(default=45, ge=10, le=180)
    proactive_questions: bool = True


class TeachingSessionControl(BaseModel):
    action: Literal["start", "pause", "resume", "stop", "seek", "complete"]
    page: int | None = Field(default=None, ge=1)


class TeachingTurnCreate(BaseModel):
    message: str = Field(default="", max_length=4000)
    action: Literal["explain", "ask", "continue"] = "ask"
    page: int = Field(default=1, ge=1)


class TeachingAnnotationItem(BaseModel):
    id: str | None = None
    page: int = Field(ge=1)
    author: Literal["student", "teacher"] = "student"
    kind: Literal["pen", "highlighter", "circle", "rectangle", "arrow", "text", "formula"]
    payload: dict[str, Any]


class TeachingAnnotationsSave(BaseModel):
    annotations: list[TeachingAnnotationItem] = Field(default_factory=list, max_length=5000)


class LearningQuestionCreate(BaseModel):
    knowledge_node_id: str | None = None
    question_type: Literal[
        "single_choice", "multiple_choice", "true_false", "fill", "short_answer", "code"
    ] = "single_choice"
    prompt: str = Field(min_length=2, max_length=20_000)
    options: list[str] = Field(default_factory=list, max_length=20)
    answer: dict[str, Any] = Field(default_factory=dict)
    rubric: dict[str, Any] = Field(default_factory=dict)
    difficulty: int = Field(default=2, ge=1, le=5)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class LearningAttemptCreate(BaseModel):
    question_id: str
    answer: Any
    agent_id: str | None = None


class LearningMistakeUpdate(BaseModel):
    correction: str | None = Field(default=None, max_length=20_000)
    status: Literal["open", "reviewing", "mastered"] | None = None
    reviewed: bool = False


class LearningMemoryCreate(BaseModel):
    category: Literal["concept", "misconception", "method", "note", "question", "preference"] = "note"
    content: str = Field(min_length=2, max_length=20_000)
    source_type: str = Field(default="user", max_length=50)
    source_id: str | None = Field(default=None, max_length=36)
    confidence: float = Field(default=1.0, ge=0, le=1)
    locked: bool = False


class LearningAssessmentGenerate(BaseModel):
    period: Literal["current", "weekly", "stage", "final"] = "current"
