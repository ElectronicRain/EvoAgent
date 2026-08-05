from __future__ import annotations

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
