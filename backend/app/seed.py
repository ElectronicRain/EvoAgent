from __future__ import annotations

from sqlalchemy import select

from .config import settings
from .db import session_scope
from .models import (
    AgentDefinition,
    AgentGroup,
    AgentRun,
    ApprovalPolicy,
    EvaluationCase,
    Extension,
    KnowledgeBase,
    KnowledgeBaseGroup,
    KnowledgeBaseGroupMember,
    Skill,
    Workflow,
    WorkflowRun,
    utcnow,
)
from .services.common import dumps, loads
from .services.knowledge import knowledge_service
from .services.model_routing import (
    latest_chat_endpoint,
    migrate_agents_to_online_endpoint,
)
from .services.workflows import WorkflowEngine


async def recover_stale_runs(db) -> None:
    """A desktop restart means no prior in-memory task can still own these rows."""
    for run in (
        await db.scalars(
            select(AgentRun).where(AgentRun.status.in_(["queued", "running"]))
        )
    ).all():
        run.status = "interrupted"
        run.error = run.error or "客户端重启，先前的 Agent 任务已结束"
    for run in (
        await db.scalars(
            select(WorkflowRun).where(
                WorkflowRun.status.in_(["queued", "running", "paused"])
            )
        )
    ).all():
        run.status = "interrupted"
        run.current_node_id = None
        run.error = run.error or "客户端重启，先前的工作流任务已结束"


async def ensure_online_agent_bindings(db) -> None:
    if not settings.require_online_agents:
        return
    endpoint = await latest_chat_endpoint(db)
    if endpoint:
        await migrate_agents_to_online_endpoint(db, endpoint)


async def upgrade_workflow_runtime_contracts(db) -> None:
    """Upgrade saved graphs without discarding user-authored nodes or positions."""
    engine = WorkflowEngine()
    for workflow in (await db.scalars(select(Workflow))).all():
        definition = loads(workflow.definition_json, {"nodes": [], "edges": []})
        nodes = list(definition.get("nodes") or [])
        edges = list(definition.get("edges") or [])
        changed = False
        has_knowledge_node = any(node.get("type") == "knowledge" for node in nodes)
        execution = definition.setdefault("execution", {})
        if execution.get("intent_validation") is not settings.require_online_agents:
            execution["intent_validation"] = settings.require_online_agents
            changed = True
        node_map = {str(node.get("id")): node for node in nodes}
        for node in nodes:
            config = node.setdefault("config", {})
            if node.get("type") == "agent":
                if "retry_count" not in config:
                    config["retry_count"] = 0
                    changed = True
                if "tool_policy" not in config:
                    config["tool_policy"] = "auto"
                    changed = True
                if "rag_mode" not in config:
                    config["rag_mode"] = "off" if has_knowledge_node else "auto"
                    changed = True
                prompt = str(config.get("prompt") or "")
                if engine.prompt_looks_corrupted(prompt):
                    config["prompt"] = engine.default_agent_node_prompt(
                        str(node.get("label") or node.get("id") or "Agent")
                    )
                    changed = True

        for condition in [node for node in nodes if node.get("type") == "condition"]:
            condition_id = str(condition.get("id"))
            condition_config = condition.setdefault("config", {})
            review_incoming = next(
                (edge for edge in edges if edge.get("target") == condition_id),
                None,
            )
            review_id = str(review_incoming.get("source")) if review_incoming else ""
            if condition_config.get("condition") and review_id:
                condition_config.pop("condition", None)
                condition_config.update(
                    {
                        "left": f"{{{{nodes.{review_id}.output}}}}",
                        "operator": "contains",
                        "right": "DECISION: PASS",
                    }
                )
                changed = True
            elif condition_config.get("right") == "通过":
                condition_config["right"] = "DECISION: PASS"
                changed = True

            reviewer = node_map.get(review_id)
            reviewer_agent_id = str(
                (reviewer or {}).get("config", {}).get("agent_id") or ""
            )
            reviewer_agent = (
                await db.get(AgentDefinition, reviewer_agent_id)
                if reviewer_agent_id
                else None
            )
            if reviewer_agent and "DECISION: PASS" not in reviewer_agent.system_prompt:
                reviewer_agent.system_prompt = (
                    f"{reviewer_agent.system_prompt.rstrip()}\n\n"
                    "审核输出首行必须只写“DECISION: PASS”或“DECISION: REVISE”；"
                    "随后再说明依据、问题和修改建议。"
                )
                changed = True

            deliverable_incoming = next(
                (edge for edge in edges if edge.get("target") == review_id),
                None,
            )
            deliverable_id = (
                str(deliverable_incoming.get("source"))
                if deliverable_incoming
                else review_id
            )
            if not deliverable_id or deliverable_id not in node_map:
                continue
            for false_edge in [
                edge
                for edge in edges
                if edge.get("source") == condition_id
                and edge.get("source_slot") == "false"
            ]:
                revision = node_map.get(str(false_edge.get("target")))
                if not revision or revision.get("type") != "agent":
                    continue
                revision_label = str(
                    revision.get("label") or revision.get("id") or "修订"
                )
                if not engine.agent_node_policy_preset(revision_label) == "review":
                    continue
                revision_config = revision.setdefault("config", {})
                desired_input = (
                    f"【待修订正文】\n{{{{nodes.{deliverable_id}.output}}}}\n\n"
                    f"【评审意见】\n{{{{nodes.{review_id}.output}}}}"
                )
                if revision_config.get("auto_input", True) or (
                    desired_input != revision_config.get("input")
                ):
                    revision_config["auto_input"] = False
                    revision_config["input"] = desired_input
                    changed = True
                revision_prompt = str(revision_config.get("prompt") or "")
                if not revision_prompt or "DECISION" in revision_prompt:
                    revision_config["prompt"] = engine.default_agent_node_prompt(
                        revision_label
                    )
                    changed = True
                if revision_config.get("tool_policy") != "review":
                    revision_config["tool_policy"] = "review"
                    changed = True
                if revision_config.get("rag_mode") != "off":
                    revision_config["rag_mode"] = "off"
                    changed = True
                if int(revision_config.get("input_context_char_limit") or 0) < 80000:
                    revision_config["input_context_char_limit"] = 80000
                    changed = True
            direct_true_edges = [
                edge
                for edge in edges
                if edge.get("source") == condition_id
                and edge.get("source_slot") == "true"
                and node_map.get(str(edge.get("target")), {}).get("type") == "merge"
            ]
            if not direct_true_edges:
                continue
            base_id = f"{condition_id}_approved_result"
            approved_id = base_id
            suffix = 2
            while approved_id in node_map:
                approved_id = f"{base_id}_{suffix}"
                suffix += 1
            condition_position = condition.get("position") or {}
            approved = {
                "id": approved_id,
                "type": "template",
                "label": "审核通过稿",
                "config": {
                    "template": f"{{{{nodes.{deliverable_id}.output}}}}"
                },
                "position": {
                    "x": float(condition_position.get("x", 900)) + 220,
                    "y": max(40, float(condition_position.get("y", 240)) - 110),
                },
            }
            nodes.append(approved)
            node_map[approved_id] = approved
            for edge in direct_true_edges:
                merge_id = edge["target"]
                edge["target"] = approved_id
                edges.append(
                    {
                        "source": approved_id,
                        "target": merge_id,
                        "source_slot": "output",
                    }
                )
            changed = True
        if changed:
            definition["nodes"] = nodes
            definition["edges"] = edges
            try:
                engine.validate_definition(definition)
            except ValueError:
                continue
            workflow.definition_json = dumps(definition)
            workflow.version += 1


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
        (
            "jsxgraph-math-visualization",
            "数学问题自动输出逐步公式推导，并用受控 JSXGraph JSON 绘制交互图表。",
            (
                "遇到代数、函数、解析几何、微积分、向量、三角、概率分布或数值方法问题时，"
                "先明确已知量、未知量、定义域和假设，再使用 $...$ 与 $$...$$ 给出逐步推导、"
                "定理依据和代回验证。当图形能帮助解释时，追加 ```jsxgraph JSON 代码块。"
                "JSON 顶层包含 title、boundingBox、axis、objects；对象只使用 "
                "functiongraph、curve、point、line、segment、arrow、polygon、circle。"
                "函数表达式只使用 x、t、pi、e、四则运算、^、括号和常见数学函数。"
                "禁止输出 JavaScript、HTML、事件处理器或外部 URL；不适合画图时不要强行生成图表。"
            ),
        ),
    ]
    skills = {}
    for name, description, instructions in skill_definitions:
        skill = await db.scalar(select(Skill).where(Skill.name == name))
        if not skill:
            skill = Skill(name=name, description=description, instructions=instructions)
            db.add(skill)
            await db.flush()
        skill.description = description
        skill.instructions = instructions
        skill.validation_status = "verified"
        skill.risk_level = "none"
        skill.validation_json = dumps(
            {
                "is_skill": True,
                "safe": True,
                "status": "verified",
                "risk_level": "none",
                "checks": {"builtin_trusted": True},
                "findings": [],
                "scanner_version": "builtin",
            }
        )
        skill.verified_at = skill.verified_at or utcnow()
        if name == "jsxgraph-math-visualization":
            skill.source_path = "builtin://skills/jsxgraph-math-visualization"
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
            "name": "科研文献研究专家",
            "slug": "research-literature-specialist",
            "status": "active",
            "group": "research",
            "description": "面向文献调研、来源筛选、研究脉络梳理与可追溯综述的通用科研专家。",
            "system_prompt": (
                "你是科研文献研究专家。围绕研究问题设计检索式，优先权威与近期来源，"
                "区分纳入、排除和待核验文献；综述必须保留题名、DOI或来源并标明事实与推论。"
            ),
            "tools": ["web_research", "list_directory", "read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("学术可信回答", "引用与事实核验", "结构化成果交付"),
        },
        {
            "name": "科研 Idea 苏格拉底导师",
            "slug": "research-idea-mentor",
            "status": "active",
            "group": "specialist",
            "description": "通过一问一答澄清研究空白、可证伪假设、创新点、反例与实验可行性。",
            "system_prompt": (
                "你是科研 Idea 苏格拉底导师。每轮先回应用户，再只提出一个最有价值的问题；"
                "检查新颖性、可证伪性、数据可得性、方法匹配和潜在反例，不替用户虚构结论。"
            ),
            "tools": ["web_research", "read_file", "search_files", "exec"],
            "skills": skill_ids("学术可信回答", "研究问题与实验设计", "引用与事实核验"),
        },
        {
            "name": "科研实验设计与复现专家",
            "slug": "research-experiment-specialist",
            "status": "active",
            "group": "specialist",
            "description": "把研究假设承接为包含变量、数据、基线、指标、统计检验和复现条件的实验。",
            "system_prompt": (
                "你是科研实验设计与复现专家。把假设转为可复现、可证伪的实验方案，明确变量、"
                "样本、基线、指标、消融、随机种子、重复次数、统计检验、失败标准和资源限制。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("研究问题与实验设计", "数据隐私与科研伦理", "结构化成果交付"),
        },
        {
            "name": "领域前沿追踪专家",
            "slug": "research-frontier-tracker",
            "status": "active",
            "group": "research",
            "description": "持续检索近期论文与研究动态，形成可追溯的热点图谱、时间趋势和证据边界。",
            "system_prompt": (
                "你是领域前沿追踪专家。围绕项目研究问题构造近期检索式，保留题名、年份、DOI、"
                "来源和可信度；热点与增长趋势必须说明统计时间窗、样本范围和推断限制，不用题录频次冒充全领域引用影响力。"
            ),
            "tools": ["web_research", "read_file", "search_files", "exec"],
            "skills": skill_ids("学术可信回答", "引用与事实核验", "结构化成果交付"),
        },
        {
            "name": "科研数据与论文图表专家",
            "slug": "research-data-figure-specialist",
            "status": "active",
            "group": "specialist",
            "description": "完成数据画像、质量检查、统计洞察与期刊风格论文图表，保留分析依据和图表质量门禁。",
            "system_prompt": (
                "你是科研数据与论文图表专家。遵循 SciPilot Figure Skill 的数据画像—论证目标—图型选择—"
                "期刊规范—视觉自检—矢量导出流程；不伪造数据，不使用 3D、彩虹色、双 Y 轴和误导性小样本均值柱，"
                "明确缺失值、样本量、相关与因果边界，并给出可复现的图注信息。"
            ),
            "tools": ["list_directory", "read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("学术可信回答", "研究问题与实验设计", "数据隐私与科研伦理", "结构化成果交付"),
        },
        {
            "name": "LaTeX 学术写作专家",
            "slug": "research-latex-writing-specialist",
            "status": "active",
            "group": "specialist",
            "description": "负责论文结构、论证、学术表达、引用一致性、LaTeX 与审稿回复。",
            "system_prompt": (
                "你是 LaTeX 学术写作专家。保持公式、命令和引用键，检查论证链、章节结构、"
                "图表和引文一致性；不得编造数据、作者、DOI或实验结果。"
            ),
            "tools": ["read_file", "search_files", "write_file", "exec"],
            "skills": skill_ids("学术可信回答", "引用与事实核验", "结构化成果交付"),
        },
        {
            "name": "模拟同行评审委员会主席",
            "slug": "research-review-chair",
            "status": "active",
            "group": "review",
            "description": "组织领域、方法、实验、统计和写作委员独立评审，量化分歧并汇总修改任务。",
            "system_prompt": (
                "你是模拟同行评审委员会主席。要求委员独立判断并提供可定位证据、量化分数、"
                "置信度和修改建议；汇总时呈现分歧与录用阈值，不替代真实同行评议。"
            ),
            "tools": ["call_agent", "read_file", "search_files", "exec"],
            "skills": skill_ids("学术可信回答", "引用与事实核验", "数据隐私与科研伦理"),
        },
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


async def ensure_computer_learning_subject_pack(
    db,
    agent_groups: dict[str, AgentGroup],
    builtin_skills: dict[str, Skill],
) -> None:
    """Install the local computer-science pack without changing user resources."""
    group = await db.scalar(
        select(KnowledgeBaseGroup).where(KnowledgeBaseGroup.name == "计算机科学学科包")
    )
    if not group:
        group = KnowledgeBaseGroup(
            name="计算机科学学科包",
            description="面向计算机基础、程序设计、软件工程、人工智能学习的本地权威知识组合。",
            color="#1769c2",
        )
        db.add(group)
        await db.flush()

    base_definitions = [
        (
            "计算机基础与 408 知识库",
            "数据结构、计算机组成原理、操作系统和计算机网络的核心概念与学习规范。",
            [
                (
                    "计算机科学课程体系与能力结构",
                    "ACM/IEEE-CS Computing Curricula 2023 · https://csed.acm.org/",
                    "ACM 与 IEEE-CS 的 Computing Curricula 2023 强调，计算机教育需要同时覆盖知识、技能和职业能力。学习设计应明确先修关系，并通过编程、分析、设计、评价与综合实践证明能力，而不只记录阅读时长。\n\nEvoAgent 学习空间据此把学习过程拆为知识节点、可执行任务、形成性练习、错题订正和阶段评测。每项生成内容保留来源标签；无法由资料确认的内容应标为推断或待核验。",
                ),
                (
                    "数据结构与算法核心规范",
                    "Cormen et al., Introduction to Algorithms, MIT Press",
                    "算法分析关注输入规模与时间、空间资源之间的增长关系。渐近记号用于描述增长上界、下界或紧确界，不能替代对常数、输入分布和实现环境的分析。\n\n数据结构的选择应由操作需求驱动：数组适合随机访问，链式结构适合局部插入删除，散列表追求期望常数时间查找，树与图用于表达层次和一般关系。作答时应同时说明正确性、复杂度、边界条件与反例。",
                ),
                (
                    "操作系统的抽象、并发与虚拟化",
                    "Arpaci-Dusseau, Operating Systems: Three Easy Pieces · https://pages.cs.wisc.edu/~remzi/OSTEP/",
                    "操作系统通过进程、地址空间和文件等抽象管理硬件资源。并发问题需要识别共享状态、临界区、原子性、互斥和同步条件；只给出锁并不能自动证明程序无死锁或无竞态。\n\n虚拟内存把进程使用的虚拟地址映射到物理内存，并通过页表、TLB 和页面置换在隔离、容量与性能之间权衡。分析系统问题应明确事件顺序、状态变化和失败边界。",
                ),
                (
                    "分层网络与可靠传输",
                    "Kurose & Ross, Computer Networking: A Top-Down Approach",
                    "网络分层用于控制复杂度，各层通过明确接口提供服务。IP 提供尽力而为的数据报服务；TCP 在其上提供面向连接的可靠字节流，并包含序号、确认、重传、流量控制与拥塞控制等机制。\n\n排查网络问题时应从应用、传输、网络和链路层逐层验证，区分域名解析、连接建立、路由、丢包、服务端状态和应用协议错误。",
                ),
            ],
        ),
        (
            "编程与软件工程知识库",
            "程序设计、调试测试、版本控制、需求设计和协作交付规范。",
            [
                (
                    "Python 语言与程序行为",
                    "Python 3 官方文档 · https://docs.python.org/3/",
                    "Python 程序设计应区分名称、对象、可变性、作用域、迭代协议和异常控制流。代码答案需要说明输入输出、类型假设、边界条件与异常行为，并尽可能给出可运行的最小示例。\n\n调试应先稳定复现，再缩小问题范围，提出可证伪假设，进行最小修改并执行回归测试。不得仅凭错误表象直接修改多个无关位置。",
                ),
                (
                    "软件工程、测试与协作交付",
                    "SWEBOK Guide, IEEE Computer Society · https://www.computer.org/education/bodies-of-knowledge/software-engineering",
                    "软件工程覆盖需求、设计、构造、测试、维护、配置管理、质量与工程管理。可验收需求应描述对象、场景、约束和成功标准；设计需要记录关键取舍与风险。\n\n测试至少应区分单元、集成、系统和验收层次。版本控制提交应保持目标单一、信息清晰和可追溯；代码审查关注正确性、安全性、可维护性、测试充分性与文档一致性。",
                ),
                (
                    "数据库事务与数据可靠性",
                    "Silberschatz et al., Database System Concepts",
                    "关系数据库使用关系模型组织数据，主键标识记录，外键表达参照关系。索引可以减少部分查询的搜索代价，但会增加存储与写入维护成本。\n\n事务的 ACID 特性分别涉及原子性、一致性、隔离性和持久性。并发控制需要结合隔离级别分析脏读、不可重复读、幻读等现象，不能把应用层校验当作完整事务保障。",
                ),
            ],
        ),
        (
            "人工智能与数据科学知识库",
            "机器学习、深度学习、RAG、实验评测与可信 AI 的基础知识。",
            [
                (
                    "机器学习实验与量化评测",
                    "James et al., An Introduction to Statistical Learning · https://www.statlearning.com/",
                    "机器学习实验需要先定义任务、数据分布、目标指标和基线。训练集用于参数学习，验证集用于模型选择，测试集用于最终泛化估计；反复依据测试集调参会导致信息泄漏。\n\n报告结果时应给出样本规模、数据划分、随机种子、指标定义、基线、方差或置信区间，并进行错误分析。单一准确率不足以描述类别不平衡任务的表现。",
                ),
                (
                    "检索增强生成与来源追溯",
                    "Lewis et al., Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks, NeurIPS 2020",
                    "RAG 先从外部知识集合检索与问题相关的证据，再将证据作为生成上下文。系统质量应分别评估检索召回、排序、上下文覆盖、回答正确性、引用完整性与忠实度。\n\n生成答案必须把结论与证据对应起来，保留文档、片段或链接标识。检索不到证据时应明确说明知识缺口，不能使用模型记忆伪造来源。",
                ),
                (
                    "可信 AI 风险管理",
                    "NIST AI Risk Management Framework 1.0 · https://www.nist.gov/itl/ai-risk-management-framework",
                    "NIST AI RMF 将可信 AI 风险管理组织为 Govern、Map、Measure 和 Manage。具体应用应识别使用情境、影响对象、数据与模型限制，建立量化测量、人工监督、事件记录和持续改进机制。\n\n教育场景中的 AI 输出不能替代教师或学习者的最终判断。高风险建议、学术结论和评价结果需要展示依据、置信度、限制与人工复核入口。",
                ),
            ],
        ),
    ]

    bases: list[KnowledgeBase] = []
    for name, description, documents in base_definitions:
        base = await db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))
        if not base:
            base = KnowledgeBase(name=name, discipline="计算机科学", description=description)
            db.add(base)
            await db.flush()
        else:
            base.description = description
        bases.append(base)
        member = await db.scalar(
            select(KnowledgeBaseGroupMember).where(
                KnowledgeBaseGroupMember.group_id == group.id,
                KnowledgeBaseGroupMember.knowledge_base_id == base.id,
            )
        )
        if not member:
            db.add(KnowledgeBaseGroupMember(group_id=group.id, knowledge_base_id=base.id))
        for title, source, content in documents:
            await knowledge_service.add_document(
                db,
                base.id,
                title=title,
                source=source,
                content=content,
                metadata={"subject_pack": "computer-science", "authority": True},
            )
    await db.flush()

    skill_ids = [
        builtin_skills[name].id
        for name in ("学术可信回答", "引用与事实核验", "结构化成果交付")
    ]
    agent_definitions = [
        ("learning-planner", "计算机学习规划 Agent", "orchestration", "把学习目标、基础水平、可用时间和截止日期转化为具有先修关系、练习与复习节奏的可执行计划。"),
        ("learning-socratic-tutor", "计算机苏格拉底辅导 Agent", "specialist", "通过连续追问帮助学习者澄清概念、暴露推理缺口，并使用学科包证据提供分层提示。"),
        ("learning-practice-designer", "计算机练习设计 Agent", "specialist", "依据知识节点、掌握度和学习目标生成选择、简答、代码与综合实践题，并附标准答案与量化评分规程。"),
        ("learning-answer-reviewer", "计算机作答批改 Agent", "review", "按标准答案、关键步骤、边界条件、复杂度和代码测试结果量化批改，区分概念错误与表达遗漏。"),
        ("learning-mistake-diagnostician", "计算机错题诊断 Agent", "review", "归因错题、提出最小补救材料和 1/3/7 天间隔复习任务，跟踪订正是否真正迁移。"),
        ("learning-assessment-coach", "计算机学习评测 Agent", "review", "综合任务完成度、练习正确率、知识掌握度、错题订正率和覆盖度生成可解释学习报告。"),
    ]
    agents: dict[str, AgentDefinition] = {}
    for slug, name, group_key, purpose in agent_definitions:
        agent = await db.scalar(select(AgentDefinition).where(AgentDefinition.slug == slug))
        prompt = (
            f"你是{name}。{purpose}所有事实性回答优先使用绑定的计算机学科知识库，"
            "结论必须给出来源标签；资料不足时明确说明待核验，不得编造定义、标准、文献或运行结果。"
            "反馈应简洁友好，结合上下文消解歧义，并通过追问确认学习者真实理解。"
        )
        if not agent:
            agent = AgentDefinition(
                group_id=agent_groups[group_key].id,
                name=name,
                slug=slug,
                description=purpose,
                system_prompt=prompt,
                provider="demo",
                model="demo-model",
                temperature=0.2,
                tools_json=dumps(["read_file", "search_files", "exec"]),
                skills_json=dumps(skill_ids),
                knowledge_bases_json=dumps([base.id for base in bases]),
                permissions_json=dumps({"tool_mode": "ask"}),
                status="active",
                is_template=True,
            )
            db.add(agent)
            await db.flush()
        agents[slug] = agent

    workflow_definitions = [
        (
            "计算机学习闭环工作流",
            "规划—辅导—练习—批改—评测的学习闭环，可由学习空间直接调用。",
            [
                ("plan", "学习规划", "learning-planner", "请根据学习任务制定可执行计划：{{input.task}}"),
                ("tutor", "概念辅导", "learning-socratic-tutor", "围绕任务进行循序辅导：{{nodes.plan.output}}"),
                ("practice", "练习设计", "learning-practice-designer", "根据辅导内容设计练习：{{nodes.tutor.output}}"),
                ("review", "作答核验", "learning-answer-reviewer", "核验计划和练习是否明确、正确、可评分：{{nodes.practice.output}}"),
            ],
        ),
        (
            "计算机错题强化工作流",
            "错因诊断—补救练习—掌握度评测的间隔复习闭环。",
            [
                ("diagnose", "错因诊断", "learning-mistake-diagnostician", "分析该错题的知识缺口：{{input.task}}"),
                ("practice", "变式练习", "learning-practice-designer", "依据错因生成由浅入深的变式题：{{nodes.diagnose.output}}"),
                ("assess", "掌握评测", "learning-assessment-coach", "给出量化验收标准和复习节奏：{{nodes.practice.output}}"),
            ],
        ),
    ]
    for name, description, steps in workflow_definitions:
        workflow = await db.scalar(select(Workflow).where(Workflow.name == name))
        if workflow:
            continue
        nodes: list[dict] = [{"id": "input", "type": "input", "label": "学习任务输入"}]
        edges: list[dict] = []
        previous = "input"
        for node_id, label, slug, prompt in steps:
            nodes.append({"id": node_id, "type": "agent", "label": label, "config": {"agent_id": agents[slug].id, "input": prompt}})
            edges.append({"source": previous, "target": node_id})
            previous = node_id
        nodes.append({"id": "output", "type": "output", "label": "学习结果", "config": {"value": "{{nodes.%s.output}}" % previous}})
        edges.append({"source": previous, "target": "output"})
        db.add(Workflow(name=name, description=description, definition_json=dumps({"nodes": nodes, "edges": edges})))

    existing_cases = set((await db.scalars(select(EvaluationCase.name).where(EvaluationCase.discipline == "计算机科学"))).all())
    cases = [
        ("算法复杂度概念测试", "解释 O(n log n) 与 O(n²) 的增长差异，并说明为何渐近复杂度不等于实际运行时间。", ["增长", "输入规模", "常数"], "quality"),
        ("操作系统并发安全测试", "两个线程修改共享计数器时为什么可能丢失更新？给出可验证的解决思路。", ["竞态", "临界区", "互斥"], "quality"),
        ("RAG 来源追溯测试", "说明检索增强生成的基本流程、评测指标和检索不到证据时的处理方式。", ["检索", "引用", "待核验"], "evidence"),
    ]
    for name, input_text, keywords, category in cases:
        if name not in existing_cases:
            db.add(EvaluationCase(name=name, discipline="计算机科学", category=category, input_text=input_text, expected_keywords_json=dumps(keywords), requires_citation=True, weight=1.5))
    await db.flush()


async def seed_demo_data() -> None:
    async with session_scope() as db:
        await recover_stale_runs(db)
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
            await ensure_computer_learning_subject_pack(db, agent_groups, builtin_skills)
            await ensure_online_agent_bindings(db)
            await upgrade_workflow_runtime_contracts(db)
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
        await ensure_computer_learning_subject_pack(db, agent_groups, builtin_skills)

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
        await ensure_online_agent_bindings(db)
        await upgrade_workflow_runtime_contracts(db)
