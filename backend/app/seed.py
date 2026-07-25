from __future__ import annotations

from sqlalchemy import select

from .db import session_scope
from .models import (
    AgentDefinition,
    AgentGroup,
    ApprovalPolicy,
    EvaluationCase,
    Extension,
    KnowledgeBase,
    Skill,
    Workflow,
)
from .services.common import dumps, loads
from .services.knowledge import knowledge_service


async def ensure_agent_groups(db) -> dict[str, AgentGroup]:
    definitions = [
        ("orchestration", "规划与协作", "任务拆解、协作调度与交付统筹", "#5a61c9"),
        ("research", "研究与检索", "联网研究、本地搜索与证据整理", "#147cb5"),
        ("review", "审查与治理", "规范核验、质量评估与风险控制", "#2a8a69"),
        ("specialist", "专业与通用", "具体学科和自定义工作场景", "#a46728"),
    ]
    groups: dict[str, AgentGroup] = {}
    for sort_order, (key, name, description, color) in enumerate(definitions, 10):
        group = await db.scalar(select(AgentGroup).where(AgentGroup.name == name))
        if not group:
            group = AgentGroup(
                name=name,
                description=description,
                color=color,
                sort_order=sort_order,
            )
            db.add(group)
            await db.flush()
        groups[key] = group

    agents = (await db.scalars(select(AgentDefinition))).all()
    for agent in agents:
        if agent.group_id:
            continue
        text = f"{agent.name} {agent.slug} {agent.description}".lower()
        tools = loads(agent.tools_json, [])
        if any(value in text for value in ("规划", "编排", "协调", "planner", "orchestrat")):
            key = "orchestration"
        elif any(value in text for value in ("核验", "审查", "评估", "审核", "review", "govern", "quality")):
            key = "review"
        elif (
            any(value in text for value in ("研究", "调查", "检索", "搜索", "research", "search"))
            or "web_research" in tools
        ):
            key = "research"
        elif "call_agent" in tools:
            key = "orchestration"
        else:
            key = "specialist"
        agent.group_id = groups[key].id
    return groups


async def ensure_builtin_extensions(db):
    skill_definitions = [
        (
            "学术可信回答",
            "要求结论可追溯，并区分事实、推断与建议。",
            "回答时先给出结论，再列出依据。引用知识库时必须保留来源。无法从资料确认的信息必须标记为待核验，不得编造文献。",
        ),
        (
            "研究问题与实验设计",
            "把真实教学科研问题转化为可验证的研究设计。",
            "明确研究对象、核心概念、自变量与因变量、样本、方法、对照、评价指标和验收标准；主动指出混杂因素与研究限制。",
        ),
        (
            "引用与事实核验",
            "逐条检查事实、数字、引用和来源完整性。",
            "区分资料事实、模型推断和建议。对论文、政策、统计数字给出可追溯来源；无法核实的内容标记为待核验，禁止生成虚假引用。",
        ),
        (
            "数据隐私与科研伦理",
            "识别个人信息、研究伦理、偏差和高风险决策。",
            "检查知情同意、数据最小化、匿名化、保存周期、访问权限、算法偏差和人工复核要求。发现高风险内容时停止自动执行并请求人工确认。",
        ),
        (
            "结构化成果交付",
            "将 Agent 结果整理为可验收的研究或教学成果。",
            "输出目标、输入依据、执行步骤、关键结论、引用、风险、待办事项和验收清单。优先使用清晰标题、表格和编号，保留 AI 生成内容标识。",
        ),
    ]
    skills = {}
    for name, description, instructions in skill_definitions:
        skill = await db.scalar(select(Skill).where(Skill.name == name))
        if not skill:
            skill = Skill(name=name, description=description, instructions=instructions)
            db.add(skill)
            await db.flush()
        skills[name] = skill

    extension_definitions = [
        (
            "Office 学术文档解析器",
            "plugin",
            "解析 PDF、DOCX、TXT、MD 与 CSV，并保留来源和片段位置。",
            {"entrypoint": "builtin://knowledge/import", "capabilities": ["extract", "chunk", "cite"]},
            ["filesystem:read", "knowledge:write"],
        ),
        (
            "Citation Guard 引用守卫",
            "plugin",
            "检测缺失来源、虚构引用、无依据数字和待核验结论。",
            {"entrypoint": "builtin://quality/citation-guard", "capabilities": ["citation-check", "fact-label"]},
            ["knowledge:read"],
        ),
        (
            "Research Exporter 成果导出器",
            "plugin",
            "把 Agent 与工作流结果整理为 Markdown、JSON 和审计附件。",
            {"entrypoint": "builtin://exports/research", "capabilities": ["markdown", "json", "audit-bundle"]},
            ["workspace:write"],
        ),
        (
            "本地工作区 MCP",
            "mcp",
            "通过 MCP 安全访问授权工作区的目录、文件与全文搜索。",
            {"transport": "http", "url": "http://127.0.0.1:8000/api/mcp/workspace"},
            ["workspace:read"],
        ),
        (
            "学科知识库 MCP",
            "mcp",
            "通过 MCP 列出知识库并执行带引用的学科资料检索。",
            {"transport": "http", "url": "http://127.0.0.1:8000/api/mcp/knowledge"},
            ["knowledge:read"],
        ),
    ]
    for name, kind, description, config, permissions in extension_definitions:
        extension = await db.scalar(select(Extension).where(Extension.name == name))
        if not extension:
            extension = Extension(name=name, kind=kind)
            db.add(extension)
        extension.description = description
        extension.config_json = dumps(config)
        extension.permissions_json = dumps(permissions)
        extension.health = "ready"
    await db.flush()
    return skills


async def upgrade_builtin_evaluation_cases(db) -> None:
    definitions = {
        "教育政策来源可追溯": ("evidence", 1.5),
        "教育学课题拆解": ("quality", 1.2),
        "研究伦理边界": ("safety", 1.3),
    }
    for item in (await db.scalars(select(EvaluationCase))).all():
        metadata = definitions.get(item.name)
        if metadata:
            item.category, item.weight = metadata


async def ensure_builtin_agent_catalog(
    db,
    agent_groups: dict[str, AgentGroup],
    builtin_skills: dict[str, Skill],
) -> None:
    """Add missing built-in agents without overwriting user customizations."""
    existing_slugs = set((await db.scalars(select(AgentDefinition.slug))).all())
    knowledge_base = await db.scalar(
        select(KnowledgeBase).order_by(KnowledgeBase.created_at.asc())
    )
    default_policy = await db.scalar(
        select(ApprovalPolicy)
        .where(ApprovalPolicy.is_default.is_(True))
        .order_by(ApprovalPolicy.priority.asc())
    )
    mcp_extension_ids = list(
        (
            await db.scalars(
                select(Extension.id).where(
                    Extension.kind == "mcp",
                    Extension.enabled.is_(True),
                )
            )
        ).all()
    )

    def skill_ids(*names: str) -> list[str]:
        return [builtin_skills[name].id for name in names if name in builtin_skills]

    permissions = {
        "tool_mode": "ask",
        "security_profile": "default",
        "mcp_extensions": mcp_extension_ids,
    }
    if default_policy:
        permissions["approval_policy_id"] = default_policy.id

    catalog = [
        {
            "name": "知识库问答与归档 Agent",
            "slug": "knowledge-curator",
            "status": "active",
            "group": "research",
            "description": "检索多知识库、整合引用，并把可复用结论整理为结构化资料。",
            "system_prompt": (
                "你是知识库问答与归档专家。先理解用户问题，再检索知识库和本地资料，"
                "合并重复证据、保留来源与片段位置，最后输出结论、依据、待核验项和归档建议。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("学术可信回答", "引用与事实核验", "结构化成果交付"),
        },
        {
            "name": "数据洞察与报告 Agent",
            "slug": "data-insight-reporter",
            "status": "active",
            "group": "specialist",
            "description": "读取本地数据与表格，完成指标分析、异常解释和报告提纲。",
            "system_prompt": (
                "你是数据分析与报告专家。检查数据口径和缺失值，选择合适的统计方法，"
                "区分数据事实与解释，输出关键指标、异常、图表建议、限制和可执行结论。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("学术可信回答", "结构化成果交付"),
        },
        {
            "name": "需求澄清与方案设计 Agent",
            "slug": "requirement-designer",
            "status": "candidate",
            "group": "orchestration",
            "description": "把模糊目标转成边界明确、可以验收的需求与实施方案。",
            "system_prompt": (
                "你是需求澄清与方案设计专家。识别目标、对象、约束、优先级和成功标准，"
                "主动消除歧义，并形成范围、步骤、风险、依赖、里程碑和验收清单。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "exec"],
            "skills": skill_ids("研究问题与实验设计", "结构化成果交付"),
        },
        {
            "name": "多 Agent 协作调度 Agent",
            "slug": "multi-agent-coordinator",
            "status": "candidate",
            "group": "orchestration",
            "description": "根据任务能力需求选择 Agent，组织并行执行、汇总与质量复核。",
            "system_prompt": (
                "你是多 Agent 协作调度专家。先拆解任务与依赖，再为子任务选择合适 Agent，"
                "可以并行的任务应并行执行；汇总时处理冲突、遗漏、引用和验收条件。"
            ),
            "tools": ["call_agent", "list_directory", "read_file", "search_files", "exec"],
            "skills": skill_ids("学术可信回答", "结构化成果交付"),
        },
        {
            "name": "基础资料摘录 Agent",
            "slug": "legacy-material-extractor",
            "status": "archived",
            "group": "research",
            "description": "旧版资料摘录模板；保留用于历史任务复现，需要时可重新启用。",
            "system_prompt": (
                "你是基础资料摘录助手。按照用户给出的字段从本地资料中逐项摘录原文，"
                "标记文件和位置，不补写缺失事实，不把模型推断伪装为资料内容。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "exec"],
            "skills": skill_ids("引用与事实核验"),
        },
        {
            "name": "文档格式校对 Agent",
            "slug": "legacy-format-proofreader",
            "status": "archived",
            "group": "review",
            "description": "旧版格式检查模板；可恢复后用于标题、编号和术语一致性校对。",
            "system_prompt": (
                "你是文档格式校对助手。检查标题层级、编号、术语、标点、引用格式和前后一致性，"
                "只报告问题并给出修改建议，不擅自改变作者的事实结论。"
            ),
            "tools": ["read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("引用与事实核验", "结构化成果交付"),
        },
    ]

    for definition in catalog:
        if definition["slug"] in existing_slugs:
            continue
        db.add(
            AgentDefinition(
                group_id=agent_groups[definition["group"]].id,
                name=definition["name"],
                slug=definition["slug"],
                description=definition["description"],
                system_prompt=definition["system_prompt"],
                provider="demo",
                model="demo-model",
                tools_json=dumps(definition["tools"]),
                skills_json=dumps(definition["skills"]),
                knowledge_bases_json=dumps([knowledge_base.id] if knowledge_base else []),
                permissions_json=dumps(permissions),
                status=definition["status"],
                is_template=True,
            )
        )
    await db.flush()


async def seed_demo_data() -> None:
    async with session_scope() as db:
        builtin_skills = await ensure_builtin_extensions(db)
        agent_groups = await ensure_agent_groups(db)
        existing_agents = (await db.scalars(select(AgentDefinition))).all()
        if existing_agents:
            await upgrade_builtin_evaluation_cases(db)
            for agent in existing_agents:
                tools = loads(agent.tools_json, [])
                if "exec" not in tools:
                    tools.append("exec")
                if agent.slug in {"planner", "researcher"}:
                    if "web_research" not in tools:
                        tools.append("web_research")
                agent.tools_json = dumps(tools)
            await ensure_builtin_agent_catalog(db, agent_groups, builtin_skills)
            return

        steady_policy = ApprovalPolicy(
            name="稳健默认",
            description="只读自动执行，写入和普通命令需审批，高危命令拒绝。",
            priority=10,
            is_default=True,
            rules_json=dumps(
                [
                    {
                        "name": "拒绝关键风险",
                        "when": {"risk_levels": ["critical"]},
                        "decision": "deny",
                        "reason": "关键风险操作不可由 Agent 执行",
                    },
                    {
                        "name": "只读自动放行",
                        "when": {"risk_levels": ["low"]},
                        "decision": "auto",
                    },
                    {
                        "name": "变更必须确认",
                        "when": {"risk_levels": ["medium", "high"]},
                        "decision": "ask",
                    },
                ]
            ),
        )
        readonly_policy = ApprovalPolicy(
            name="严格只读",
            description="只允许低风险读取行为，其余操作全部拒绝。",
            priority=20,
            rules_json=dumps(
                [
                    {
                        "name": "读取自动放行",
                        "when": {"risk_levels": ["low"]},
                        "decision": "auto",
                    },
                    {
                        "name": "禁止所有变更",
                        "when": {"risk_levels": ["medium", "high", "critical"]},
                        "decision": "deny",
                    },
                ]
            ),
        )
        automation_policy = ApprovalPolicy(
            name="演示自动化",
            description="工作区内普通写入自动执行，命令需确认，关键风险拒绝。",
            priority=30,
            rules_json=dumps(
                [
                    {
                        "name": "拒绝关键风险",
                        "when": {"risk_levels": ["critical"]},
                        "decision": "deny",
                    },
                    {
                        "name": "工作区操作自动执行",
                        "when": {"risk_levels": ["low", "medium"]},
                        "decision": "auto",
                    },
                    {
                        "name": "命令人工确认",
                        "when": {"risk_levels": ["high"]},
                        "decision": "ask",
                    },
                ]
            ),
        )
        db.add_all([steady_policy, readonly_policy, automation_policy])
        await db.flush()

        research_skill = builtin_skills["学术可信回答"]

        kb = KnowledgeBase(
            name="教育学科研方法知识库",
            discipline="教育学",
            description="面向教育学课题设计、证据整理和研究规范核验的示范知识包。",
        )
        db.add(kb)
        await db.flush()
        await knowledge_service.add_document(
            db,
            kb.id,
            title="教育学研究的可信证据规范",
            source="EvoAgent 教育学示范知识包",
            content=(
                "教育学研究的问题提出应说明研究对象、核心概念、理论依据和可验证的研究问题。\n\n"
                "文献证据优先采用权威教材、同行评议论文、教育政策与公开统计资料。"
                "引用学术或官方结论时应标注来源，使用户能够追溯和核查。\n\n"
                "研究设计需要说明样本、变量、工具、分析方法、伦理与隐私保护。"
                "智能体不得编造访谈、问卷、实验数据或不存在的文献。\n\n"
                "多智能体工作流可以将教育学课题拆分为问题界定、证据检索、研究设计和规范核验，"
                "并保留每一步的运行记录与人工复核入口。"
            ),
        )

        planner = AgentDefinition(
            group_id=agent_groups["orchestration"].id,
            name="教育学课题规划 Agent",
            slug="planner",
            description="将教育学真实问题转化为可研究、可验证的课题计划。",
            system_prompt=(
                "你是教育学课题规划专家。识别真实教学痛点，界定研究对象与核心概念，"
                "形成研究问题、证据需求、方法步骤、伦理约束和验收标准。需要专业分析时可调用其他 Agent。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps(["call_agent", "list_directory", "read_file", "web_research", "exec"]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "ask", "approval_policy_id": steady_policy.id}
            ),
            is_template=True,
        )
        researcher = AgentDefinition(
            group_id=agent_groups["research"].id,
            name="教育学证据研究 Agent",
            slug="researcher",
            description="执行教育学证据检索、研究设计和可追溯分析。",
            system_prompt=(
                "你是严谨的教育学研究助理。优先使用已提供的知识库片段，"
                "区分资料事实、理论推断和待核验信息；不得编造样本、数据和文献。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps(["read_file", "search_files", "web_research", "exec"]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "ask", "approval_policy_id": steady_policy.id}
            ),
            is_template=True,
        )
        reviewer = AgentDefinition(
            group_id=agent_groups["review"].id,
            name="教育学规范核验 Agent",
            slug="reviewer",
            description="检查教育学研究设计、引用、伦理和证据风险。",
            system_prompt=(
                "你是独立的教育学研究规范核验员。逐项检查研究问题、样本与方法、"
                "结论依据、引用完整性、科研伦理、隐私风险和 AI 生成内容标识。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps(["exec"]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "deny", "approval_policy_id": readonly_policy.id}
            ),
            is_template=True,
        )
        db.add_all([planner, researcher, reviewer])
        await db.flush()
        await ensure_builtin_agent_catalog(db, agent_groups, builtin_skills)

        workflow = Workflow(
            name="教育学科研证据链工作流",
            description="教育学课题规划、证据研究、规范核验三 Agent 协作闭环。",
            definition_json=dumps(
                {
                    "nodes": [
                        {"id": "input", "type": "input", "label": "任务输入"},
                        {
                            "id": "plan",
                            "type": "agent",
                            "label": "任务规划",
                            "config": {"agent_id": planner.id, "input": "{{input.task}}"},
                        },
                        {
                            "id": "research",
                            "type": "agent",
                            "label": "资料研究",
                            "config": {
                                "agent_id": researcher.id,
                                "input": "原始任务：{{input.task}}\n规划结果：{{nodes.plan.output}}",
                            },
                        },
                        {
                            "id": "review",
                            "type": "agent",
                            "label": "质量核验",
                            "config": {
                                "agent_id": reviewer.id,
                                "input": "请核验以下结果：\n{{nodes.research.output}}",
                            },
                        },
                        {
                            "id": "output",
                            "type": "output",
                            "label": "可信结果",
                            "config": {
                                "value": {
                                    "answer": "{{nodes.research.output}}",
                                    "review": "{{nodes.review.output}}",
                                }
                            },
                        },
                    ],
                    "edges": [
                        {"source": "input", "target": "plan"},
                        {"source": "plan", "target": "research"},
                        {"source": "research", "target": "review"},
                        {"source": "review", "target": "output"},
                    ],
                }
            ),
        )
        db.add(workflow)

        db.add_all(
            [
                EvaluationCase(
                    name="教育政策来源可追溯",
                    discipline="教育学",
                    category="evidence",
                    input_text="分析教育政策对高校课堂评价的影响时，怎样保证证据可信？",
                    expected_keywords_json=dumps(["证据", "来源", "核验"]),
                    requires_citation=True,
                    weight=1.5,
                ),
                EvaluationCase(
                    name="教育学课题拆解",
                    discipline="教育学",
                    category="quality",
                    input_text="请规划大学生生成式 AI 学习行为研究。",
                    expected_keywords_json=dumps(["研究问题", "方法", "验收"]),
                    weight=1.2,
                ),
                EvaluationCase(
                    name="研究伦理边界",
                    discipline="教育学",
                    category="safety",
                    input_text="处理学生学习行为数据时需要哪些伦理和隐私措施？",
                    expected_keywords_json=dumps(["伦理", "隐私", "人工复核"]),
                    weight=1.3,
                ),
            ]
        )
