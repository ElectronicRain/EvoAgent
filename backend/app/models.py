from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentGroup(TimestampMixin, Base):
    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="#1769c2")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class AgentDefinition(TimestampMixin, Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    lineage_id: Mapped[str] = mapped_column(String(36), index=True, default=new_id)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    model_endpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_endpoints.id"), nullable=True
    )
    image_model_endpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_endpoints.id"), nullable=True
    )
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(50), default="demo")
    model: Mapped[str] = mapped_column(String(120), default="demo-model")
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    skills_json: Mapped[str] = mapped_column(Text, default="[]")
    knowledge_bases_json: Mapped[str] = mapped_column(Text, default="[]")
    rag_config_json: Mapped[str] = mapped_column(Text, default="{}")
    generation_config_json: Mapped[str] = mapped_column(Text, default="{}")
    permissions_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)

    parent: Mapped["AgentDefinition | None"] = relationship(remote_side=[id])
    group: Mapped["AgentGroup | None"] = relationship()


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text, default="")
    trace_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    security_json: Mapped[str] = mapped_column(Text, default="{}")


class AgentConversation(TimestampMixin, Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160), default="新会话")


class AgentMessage(TimestampMixin, Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    trace_json: Mapped[str] = mapped_column(Text, default="[]")


class UserAccount(TimestampMixin, Base):
    __tablename__ = "user_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(Text)
    avatar_color: Mapped[str] = mapped_column(String(20), default="#1769c2")
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserPreference(TimestampMixin, Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    reply_style_id: Mapped[str] = mapped_column(String(30), default="balanced")
    custom_reply_style: Mapped[str] = mapped_column(Text, default="")
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_json: Mapped[str] = mapped_column(Text, default="{}")


class UserQuestionMemory(TimestampMixin, Base):
    __tablename__ = "user_question_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), default="general", index=True)
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")


class AgentArtifact(TimestampMixin, Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversations.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), default="markdown")
    title: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)


class ResearchSourceReview(TimestampMixin, Base):
    __tablename__ = "research_source_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("agent_conversations.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(20), default="confirmed", index=True)
    credibility_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchProject(TimestampMixin, Base):
    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), index=True)
    discipline: Mapped[str] = mapped_column(String(100), default="计算机科学")
    description: Mapped[str] = mapped_column(Text, default="")
    research_question: Mapped[str] = mapped_column(Text, default="")
    expected_outcome: Mapped[str] = mapped_column(Text, default="论文")
    citation_style: Mapped[str] = mapped_column(String(30), default="GB/T 7714")
    language: Mapped[str] = mapped_column(String(20), default="zh-CN")
    stage: Mapped[str] = mapped_column(String(30), default="literature", index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchProjectMember(TimestampMixin, Base):
    __tablename__ = "research_project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="editor", index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)


class ResearchProjectInvite(TimestampMixin, Base):
    __tablename__ = "research_project_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_hint: Mapped[str] = mapped_column(String(20), default="")
    role: Mapped[str] = mapped_column(String(20), default="editor", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    max_uses: Mapped[int] = mapped_column(Integer, default=20)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)


class ResearchProjectLedger(Base):
    __tablename__ = "research_project_ledger"
    __table_args__ = (UniqueConstraint("project_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(60), default="research_project")
    resource_id: Mapped[str] = mapped_column(String(100), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    entry_hash: Mapped[str] = mapped_column(String(64), index=True)


class ResearchProjectResource(TimestampMixin, Base):
    __tablename__ = "research_project_resources"
    __table_args__ = (UniqueConstraint("project_id", "resource_type", "resource_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(40), index=True)
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    label: Mapped[str] = mapped_column(String(240), default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchLiterature(TimestampMixin, Base):
    __tablename__ = "research_literature"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    doi: Mapped[str] = mapped_column(String(300), default="", index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(120), default="手动录入")
    abstract: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    credibility: Mapped[int] = mapped_column(Integer, default=50)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchIdea(TimestampMixin, Base):
    __tablename__ = "research_ideas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240))
    problem: Mapped[str] = mapped_column(Text, default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    novelty: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    scores_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="exploring", index=True)


class ResearchMemory(TimestampMixin, Base):
    __tablename__ = "research_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(50), default="decision", index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), default="user")
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class ResearchExperiment(TimestampMixin, Base):
    __tablename__ = "research_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    idea_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_ideas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240))
    objective: Mapped[str] = mapped_column(Text, default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    design_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="planned", index=True)


class ResearchArtifact(TimestampMixin, Base):
    __tablename__ = "research_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), default="markdown", index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    source_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchManuscript(TimestampMixin, Base):
    __tablename__ = "research_manuscripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240))
    format: Mapped[str] = mapped_column(String(20), default="latex")
    content: Mapped[str] = mapped_column(Text, default="")
    main_file: Mapped[str] = mapped_column(String(500), default="main.tex")
    files_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    bibliography: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")


class ResearchManuscriptVersion(TimestampMixin, Base):
    __tablename__ = "research_manuscript_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    manuscript_id: Mapped[str] = mapped_column(
        ForeignKey("research_manuscripts.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    main_file: Mapped[str] = mapped_column(String(500), default="main.tex")
    files_json: Mapped[str] = mapped_column(Text, default="{}")
    bibliography: Mapped[str] = mapped_column(Text, default="")
    change_summary: Mapped[str] = mapped_column(Text, default="")


class ResearchComment(TimestampMixin, Base):
    __tablename__ = "research_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    manuscript_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_manuscripts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_comments.id", ondelete="CASCADE"), nullable=True
    )
    file_path: Mapped[str] = mapped_column(String(500), default="main.tex")
    anchored_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)


class ResearchReview(TimestampMixin, Base):
    __tablename__ = "research_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    manuscript_id: Mapped[str] = mapped_column(
        ForeignKey("research_manuscripts.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True
    )
    round: Mapped[int] = mapped_column(Integer, default=1)
    roles_json: Mapped[str] = mapped_column(Text, default="[]")
    summary: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    scores_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)


class ResearchReviewItem(TimestampMixin, Base):
    __tablename__ = "research_review_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    review_id: Mapped[str] = mapped_column(
        ForeignKey("research_reviews.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(60), default="method")
    reviewer_role: Mapped[str] = mapped_column(String(40), default="committee")
    severity: Mapped[str] = mapped_column(String(20), default="major", index=True)
    location: Mapped[str] = mapped_column(String(240), default="")
    issue: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    response: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)


class ResearchPresence(TimestampMixin, Base):
    __tablename__ = "research_presence"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    page: Mapped[str] = mapped_column(String(80), default="overview")
    cursor_json: Mapped[str] = mapped_column(Text, default="{}")


class LearningProject(TimestampMixin, Base):
    __tablename__ = "learning_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), index=True)
    project_type: Mapped[str] = mapped_column(String(40), default="course", index=True)
    discipline: Mapped[str] = mapped_column(String(100), default="计算机科学")
    description: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[str] = mapped_column(Text, default="")
    current_level: Mapped[str] = mapped_column(String(30), default="beginner")
    target_level: Mapped[str] = mapped_column(String(30), default="proficient")
    weekly_hours: Mapped[float] = mapped_column(Float, default=6.0)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stage: Mapped[str] = mapped_column(String(30), default="planning", index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    knowledge_group_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_base_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    knowledge_base_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    agent_bindings_json: Mapped[str] = mapped_column(Text, default="{}")
    workflow_bindings_json: Mapped[str] = mapped_column(Text, default="{}")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")


class LearningKnowledgeNode(TimestampMixin, Base):
    __tablename__ = "learning_knowledge_nodes"
    __table_args__ = (UniqueConstraint("project_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(240))
    domain: Mapped[str] = mapped_column(String(100), default="计算机基础", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    prerequisites_json: Mapped[str] = mapped_column(Text, default="[]")
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    mastery: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(30), default="not_started", index=True)


class LearningTask(TimestampMixin, Base):
    __tablename__ = "learning_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    module: Mapped[str] = mapped_column(String(40), default="learn", index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text, default="")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=45)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(40), default="learning_plan")


class LearningTutorTurn(TimestampMixin, Base):
    __tablename__ = "learning_tutor_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    mode: Mapped[str] = mapped_column(String(30), default="socratic")
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class LearningQuestion(TimestampMixin, Base):
    __tablename__ = "learning_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    generated_by_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    question_type: Mapped[str] = mapped_column(String(30), default="single_choice", index=True)
    prompt: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    answer_json: Mapped[str] = mapped_column(Text, default="{}")
    rubric_json: Mapped[str] = mapped_column(Text, default="{}")
    difficulty: Mapped[int] = mapped_column(Integer, default=2, index=True)
    source_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)


class LearningAttempt(TimestampMixin, Base):
    __tablename__ = "learning_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(
        ForeignKey("learning_questions.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    answer_json: Mapped[str] = mapped_column(Text, default="{}")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    feedback: Mapped[str] = mapped_column(Text, default="")
    error_type: Mapped[str] = mapped_column(String(50), default="")
    rubric_result_json: Mapped[str] = mapped_column(Text, default="{}")


class LearningMistake(TimestampMixin, Base):
    __tablename__ = "learning_mistakes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("learning_attempts.id", ondelete="CASCADE"), unique=True, index=True
    )
    knowledge_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cause: Mapped[str] = mapped_column(Text, default="")
    correction: Mapped[str] = mapped_column(Text, default="")
    review_count: Mapped[int] = mapped_column(Integer, default=0)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)


class LearningMemory(TimestampMixin, Base):
    __tablename__ = "learning_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(50), default="concept", index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), default="user")
    source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class LearningAssessment(TimestampMixin, Base):
    __tablename__ = "learning_assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("learning_projects.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[str] = mapped_column(String(30), default="current", index=True)
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str] = mapped_column(Text, default="")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    definition_json: Mapped[str] = mapped_column(Text, default='{"nodes":[],"edges":[]}')
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    trace_json: Mapped[str] = mapped_column(Text, default="[]")
    control_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    iteration_count: Mapped[int] = mapped_column(Integer, default=0)
    current_node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class WorkflowArtifact(TimestampMixin, Base):
    __tablename__ = "workflow_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    iteration: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(30), default="markdown")
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    discipline: Mapped[str] = mapped_column(String(100), default="通用")
    description: Mapped[str] = mapped_column(Text, default="")
    document_count: Mapped[int] = mapped_column(Integer, default=0)


class KnowledgeBaseGroup(TimestampMixin, Base):
    __tablename__ = "knowledge_base_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="#1769c2")


class KnowledgeBaseGroupMember(Base):
    __tablename__ = "knowledge_base_group_members"

    group_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base_groups.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeDocument(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(Text, default="")
    mime_type: Mapped[str] = mapped_column(String(100), default="text/plain")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id"), nullable=True, index=True
    )
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    cleaning_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="ready", index=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text, default="")
    parent_chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_chunks.id"), nullable=True, index=True
    )
    level: Mapped[str] = mapped_column(String(20), default="child", index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeSource(TimestampMixin, Base):
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(30), index=True)
    uri: Mapped[str] = mapped_column(Text, default="")
    config_ciphertext: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeIngestionJob(TimestampMixin, Base):
    __tablename__ = "knowledge_ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_sources.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(50), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50), default="siliconflow")
    model: Mapped[str] = mapped_column(String(180), index=True)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    norm: Mapped[float] = mapped_column(Float, default=1.0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeProviderConfig(TimestampMixin, Base):
    __tablename__ = "knowledge_provider_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    embedding_base_url: Mapped[str] = mapped_column(
        Text, default="https://api.siliconflow.cn/v1/embeddings"
    )
    embedding_model: Mapped[str] = mapped_column(String(180), default="Qwen/Qwen3-VL-Embedding-8B")
    rerank_base_url: Mapped[str] = mapped_column(
        Text, default="https://api.siliconflow.cn/v1/rerank"
    )
    rerank_model: Mapped[str] = mapped_column(String(180), default="BAAI/bge-reranker-v2-m3")
    api_key_ciphertext: Mapped[str] = mapped_column(Text, default="")
    llm_endpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_endpoints.id"), nullable=True
    )
    embedding_batch_size: Mapped[int] = mapped_column(Integer, default=16)
    candidate_k: Mapped[int] = mapped_column(Integer, default=30)
    top_k: Mapped[int] = mapped_column(Integer, default=6)
    context_char_budget: Mapped[int] = mapped_column(Integer, default=12000)


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(30), default="1.0.0")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_path: Mapped[str] = mapped_column(Text, default="")
    validation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="unknown")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Extension(TimestampMixin, Base):
    __tablename__ = "extensions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    permissions_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(30), default="unknown")


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("approval_policies.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    execution_result_json: Mapped[str] = mapped_column(Text, default="{}")
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeSecurityConfig(TimestampMixin, Base):
    __tablename__ = "runtime_security_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    filesystem_mode: Mapped[str] = mapped_column(String(30), default="workspace")
    workspace_roots_json: Mapped[str] = mapped_column(Text, default="[]")
    command_mode: Mapped[str] = mapped_column(String(30), default="risk_based")
    block_critical_commands: Mapped[bool] = mapped_column(Boolean, default=True)


class ApprovalPolicy(TimestampMixin, Base):
    __tablename__ = "approval_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    rules_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ModelEndpoint(TimestampMixin, Base):
    __tablename__ = "model_endpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    modality: Mapped[str] = mapped_column(String(30), default="chat", index=True)
    provider_type: Mapped[str] = mapped_column(String(50), default="openai-compatible")
    base_url: Mapped[str] = mapped_column(Text)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, default="")
    default_model: Mapped[str] = mapped_column(String(160))
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    request_options_json: Mapped[str] = mapped_column(Text, default="{}")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[str] = mapped_column(String(30), default="unknown")


class EvolutionProposal(TimestampMixin, Base):
    __tablename__ = "evolution_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    candidate_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    changes_json: Mapped[str] = mapped_column(Text, default="{}")
    goal_json: Mapped[str] = mapped_column(Text, default="{}")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    baseline_score: Mapped[float] = mapped_column(Float, default=0)
    candidate_score: Mapped[float] = mapped_column(Float, default=0)
    report_json: Mapped[str] = mapped_column(Text, default="{}")


class EvaluationCase(TimestampMixin, Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160))
    discipline: Mapped[str] = mapped_column(String(100), default="通用")
    category: Mapped[str] = mapped_column(String(60), default="quality", index=True)
    input_text: Mapped[str] = mapped_column(Text)
    expected_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    requires_citation: Mapped[bool] = mapped_column(Boolean, default=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    success: Mapped[bool] = mapped_column(Boolean, default=True)
