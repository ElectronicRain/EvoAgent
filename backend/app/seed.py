from __future__ import annotations

from sqlalchemy import select

from .db import session_scope
from .models import (
    AgentDefinition,
    ApprovalPolicy,
    EvaluationCase,
    Extension,
    KnowledgeBase,
    Skill,
    Workflow,
)
from .services.common import dumps, loads
from .services.knowledge import knowledge_service


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


async def seed_demo_data() -> None:
    async with session_scope() as db:
        builtin_skills = await ensure_builtin_extensions(db)
        existing_agents = (await db.scalars(select(AgentDefinition))).all()
        if existing_agents:
            for agent in existing_agents:
                if agent.slug in {"planner", "researcher"}:
                    tools = loads(agent.tools_json, [])
                    if "web_research" not in tools:
                        agent.tools_json = dumps([*tools, "web_research"])
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
            name="教育学课题规划 Agent",
            slug="planner",
            description="将教育学真实问题转化为可研究、可验证的课题计划。",
            system_prompt=(
                "你是教育学课题规划专家。识别真实教学痛点，界定研究对象与核心概念，"
                "形成研究问题、证据需求、方法步骤、伦理约束和验收标准。需要专业分析时可调用其他 Agent。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps(["call_agent", "list_directory", "read_file", "web_research"]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "ask", "approval_policy_id": steady_policy.id}
            ),
            is_template=True,
        )
        researcher = AgentDefinition(
            name="教育学证据研究 Agent",
            slug="researcher",
            description="执行教育学证据检索、研究设计和可追溯分析。",
            system_prompt=(
                "你是严谨的教育学研究助理。优先使用已提供的知识库片段，"
                "区分资料事实、理论推断和待核验信息；不得编造样本、数据和文献。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps(["read_file", "search_files", "web_research"]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "ask", "approval_policy_id": steady_policy.id}
            ),
            is_template=True,
        )
        reviewer = AgentDefinition(
            name="教育学规范核验 Agent",
            slug="reviewer",
            description="检查教育学研究设计、引用、伦理和证据风险。",
            system_prompt=(
                "你是独立的教育学研究规范核验员。逐项检查研究问题、样本与方法、"
                "结论依据、引用完整性、科研伦理、隐私风险和 AI 生成内容标识。"
            ),
            provider="demo",
            model="demo-model",
            tools_json=dumps([]),
            skills_json=dumps([research_skill.id]),
            knowledge_bases_json=dumps([kb.id]),
            permissions_json=dumps(
                {"tool_mode": "deny", "approval_policy_id": readonly_policy.id}
            ),
            is_template=True,
        )
        db.add_all([planner, researcher, reviewer])
        await db.flush()

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
                    input_text="分析教育政策对高校课堂评价的影响时，怎样保证证据可信？",
                    expected_keywords_json=dumps(["证据", "来源", "核验"]),
                    requires_citation=True,
                ),
                EvaluationCase(
                    name="教育学课题拆解",
                    discipline="教育学",
                    input_text="请规划大学生生成式 AI 学习行为研究。",
                    expected_keywords_json=dumps(["研究问题", "方法", "验收"]),
                ),
                EvaluationCase(
                    name="研究伦理边界",
                    discipline="教育学",
                    input_text="处理学生学习行为数据时需要哪些伦理和隐私措施？",
                    expected_keywords_json=dumps(["伦理", "隐私", "人工复核"]),
                ),
            ]
        )
