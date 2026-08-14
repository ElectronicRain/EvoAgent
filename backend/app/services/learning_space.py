from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AgentDefinition,
    KnowledgeBase,
    KnowledgeBaseGroup,
    KnowledgeBaseGroupMember,
    KnowledgeChunk,
    LearningAssessment,
    LearningAttempt,
    LearningKnowledgeNode,
    LearningMemory,
    LearningMistake,
    LearningProject,
    LearningQuestion,
    LearningTask,
    LearningTutorTurn,
    UserAccount,
    Workflow,
)
from .agents import agent_engine
from .common import dumps, loads


COMPUTER_PACK_GROUP = "计算机科学学科包"
AGENT_SLUGS = {
    "planning": "learning-planner",
    "tutor": "learning-socratic-tutor",
    "practice": "learning-practice-designer",
    "review": "learning-answer-reviewer",
    "mistake": "learning-mistake-diagnostician",
    "assessment": "learning-assessment-coach",
}
WORKFLOW_NAMES = {
    "learning_loop": "计算机学习闭环工作流",
    "mistake_review": "计算机错题强化工作流",
}

TRACK_NODES: dict[str, list[dict[str, Any]]] = {
    "计算机基础": [
        {"code": "cs-foundation", "title": "计算机系统基础", "domain": "计算机基础", "description": "信息表示、程序执行与软硬件协同。", "prerequisites": []},
        {"code": "programming", "title": "程序设计基础", "domain": "程序设计", "description": "变量、控制结构、函数、抽象和调试。", "prerequisites": ["cs-foundation"]},
        {"code": "data-structures", "title": "数据结构", "domain": "数据结构与算法", "description": "线性表、树、图、散列与基本操作。", "prerequisites": ["programming"]},
        {"code": "algorithms", "title": "算法设计与复杂度", "domain": "数据结构与算法", "description": "复杂度、分治、动态规划、贪心和图算法。", "prerequisites": ["data-structures"]},
        {"code": "architecture", "title": "计算机组成原理", "domain": "计算机系统", "description": "指令、处理器、存储层次与输入输出。", "prerequisites": ["cs-foundation"]},
        {"code": "operating-systems", "title": "操作系统", "domain": "计算机系统", "description": "进程、并发、虚拟内存和文件系统。", "prerequisites": ["architecture", "data-structures"]},
        {"code": "networks", "title": "计算机网络", "domain": "计算机系统", "description": "分层协议、可靠传输、路由和应用层协议。", "prerequisites": ["cs-foundation"]},
        {"code": "databases", "title": "数据库系统", "domain": "软件与数据", "description": "关系模型、SQL、索引、事务与恢复。", "prerequisites": ["data-structures"]},
        {"code": "software-engineering", "title": "软件工程", "domain": "软件与数据", "description": "需求、设计、测试、版本控制和协作。", "prerequisites": ["programming"]},
        {"code": "security", "title": "计算机安全基础", "domain": "安全与伦理", "description": "威胁建模、认证授权、密码学基础和安全开发。", "prerequisites": ["networks", "operating-systems"]},
        {"code": "ai-foundation", "title": "人工智能基础", "domain": "人工智能", "description": "搜索、机器学习、评价方法和可信 AI。", "prerequisites": ["algorithms"]},
        {"code": "capstone", "title": "综合实践项目", "domain": "综合实践", "description": "将需求、实现、测试和复盘整合为可验收成果。", "prerequisites": ["software-engineering", "databases"]},
    ],
    "程序设计": [
        {"code": "python-basics", "title": "Python 语言基础", "domain": "程序设计", "description": "语法、数据类型、函数与模块。", "prerequisites": []},
        {"code": "debugging", "title": "程序调试与测试", "domain": "程序设计", "description": "错误定位、单元测试、边界条件和可观测性。", "prerequisites": ["python-basics"]},
        {"code": "oop", "title": "面向对象程序设计", "domain": "程序设计", "description": "封装、继承、组合、接口与设计原则。", "prerequisites": ["python-basics"]},
        {"code": "data-structures", "title": "数据结构", "domain": "数据结构与算法", "description": "列表、栈、队列、树、图和散列表。", "prerequisites": ["python-basics"]},
        {"code": "algorithms", "title": "算法与复杂度", "domain": "数据结构与算法", "description": "复杂度分析和常用算法范式。", "prerequisites": ["data-structures"]},
        {"code": "database-app", "title": "数据库应用开发", "domain": "软件工程", "description": "数据建模、SQL、事务与应用集成。", "prerequisites": ["oop"]},
        {"code": "web-api", "title": "Web 与 API 开发", "domain": "软件工程", "description": "HTTP、REST、服务端架构与安全边界。", "prerequisites": ["oop", "database-app"]},
        {"code": "engineering", "title": "工程化与协作", "domain": "软件工程", "description": "Git、代码审查、持续集成、文档与发布。", "prerequisites": ["debugging"]},
    ],
    "人工智能": [
        {"code": "math-ai", "title": "AI 数学基础", "domain": "人工智能", "description": "线性代数、概率统计和优化基础。", "prerequisites": []},
        {"code": "python-data", "title": "Python 数据处理", "domain": "人工智能", "description": "数组、数据清洗、可视化与可复现实验。", "prerequisites": []},
        {"code": "ml", "title": "机器学习基础", "domain": "人工智能", "description": "监督学习、无监督学习、泛化与评价。", "prerequisites": ["math-ai", "python-data"]},
        {"code": "deep-learning", "title": "深度学习", "domain": "人工智能", "description": "神经网络、反向传播、正则化与训练诊断。", "prerequisites": ["ml"]},
        {"code": "nlp", "title": "自然语言处理与大模型", "domain": "人工智能", "description": "表示学习、Transformer、提示与 RAG。", "prerequisites": ["deep-learning"]},
        {"code": "evaluation", "title": "模型评测与实验设计", "domain": "人工智能", "description": "指标、基线、消融、显著性与误差分析。", "prerequisites": ["ml"]},
        {"code": "trustworthy-ai", "title": "可信与负责任 AI", "domain": "安全与伦理", "description": "可靠性、公平性、隐私、透明度与治理。", "prerequisites": ["evaluation"]},
        {"code": "ai-project", "title": "AI 综合实践", "domain": "综合实践", "description": "从问题定义到部署评测的完整项目。", "prerequisites": ["nlp", "evaluation"]},
    ],
}

# Direction-specific nodes complement the curriculum skeleton above.  They are
# deliberately kept as structured data so that the same direction profile can
# drive the path, plan, questions, tutor and assessment consistently.
SPECIALIZED_NODES: list[dict[str, Any]] = [
    {"code": "web-frontend", "title": "Web 前端与交互", "domain": "Web 全栈", "description": "HTML、CSS、JavaScript、组件化与可用性交互。", "prerequisites": ["programming"]},
    {"code": "backend-service", "title": "后端服务与 API 设计", "domain": "Web 全栈", "description": "服务端分层、REST API、校验、错误处理与可观测性。", "prerequisites": ["programming", "databases"]},
    {"code": "auth-security", "title": "身份认证与应用安全", "domain": "Web 全栈", "description": "认证、授权、会话、常见 Web 风险与安全边界。", "prerequisites": ["networks"]},
    {"code": "deployment", "title": "部署、运维与持续交付", "domain": "软件工程", "description": "容器、环境配置、持续集成、日志与发布回滚。", "prerequisites": ["software-engineering"]},
    {"code": "concurrency", "title": "并发编程与同步", "domain": "计算机系统", "description": "线程、互斥、条件变量、死锁与并发正确性。", "prerequisites": ["operating-systems"]},
    {"code": "distributed-systems", "title": "分布式系统基础", "domain": "计算机系统", "description": "复制、一致性、容错、消息通信与分布式事务。", "prerequisites": ["networks", "databases"]},
    {"code": "advanced-database", "title": "数据库性能与工程实践", "domain": "软件与数据", "description": "执行计划、索引设计、并发控制、缓存与容量治理。", "prerequisites": ["databases"]},
    {"code": "network-practice", "title": "网络协议分析与实践", "domain": "计算机系统", "description": "TCP/IP、HTTP、抓包分析、网络排错与性能测量。", "prerequisites": ["networks"]},
    {"code": "cybersecurity", "title": "网络安全与攻防基础", "domain": "安全与伦理", "description": "威胁建模、漏洞原理、安全测试、修复与合规记录。", "prerequisites": ["security"]},
    {"code": "rag-agents", "title": "RAG 与智能体系统", "domain": "人工智能", "description": "知识切分、检索、重排、工具调用、引用与智能体评测。", "prerequisites": ["nlp", "evaluation"]},
    {"code": "computer-vision", "title": "计算机视觉", "domain": "人工智能", "description": "图像表示、卷积网络、检测分割与视觉模型评测。", "prerequisites": ["deep-learning"]},
    {"code": "data-science", "title": "数据分析与统计建模", "domain": "数据科学", "description": "数据清洗、探索分析、统计推断、可视化与可复现报告。", "prerequisites": ["python-data"]},
    {"code": "recommendation", "title": "推荐与检索系统", "domain": "人工智能", "description": "召回、排序、离线指标、在线实验与反馈闭环。", "prerequisites": ["ml", "evaluation"]},
    {"code": "coding-interview", "title": "算法题型与编码训练", "domain": "数据结构与算法", "description": "高频数据结构、算法模板、复杂度证明与限时编码。", "prerequisites": ["algorithms"]},
    {"code": "exam-synthesis", "title": "知识综合与应试迁移", "domain": "综合实践", "description": "考点映射、真题归因、限时训练、错题回溯与综合复盘。", "prerequisites": []},
    {"code": "project-delivery", "title": "方向成果交付", "domain": "综合实践", "description": "将需求、设计、实现、测试、演示和复盘组织为可验收成果。", "prerequisites": []},
]

DIRECTION_RULES: dict[str, dict[str, Any]] = {
    "web_fullstack": {"label": "Web 全栈与应用开发", "markers": ("web", "网站", "前端", "后端", "全栈", "vue", "react", "api", "电商", "管理系统"), "codes": ("programming", "debugging", "databases", "software-engineering", "web-frontend", "backend-service", "auth-security", "deployment", "project-delivery")},
    "systems": {"label": "系统与并发", "markers": ("操作系统", "并发", "线程", "进程", "组成原理", "系统编程", "内核", "408"), "codes": ("cs-foundation", "architecture", "data-structures", "operating-systems", "concurrency", "networks", "software-engineering", "exam-synthesis")},
    "algorithms": {"label": "算法与数据结构", "markers": ("算法", "数据结构", "leetcode", "竞赛", "复杂度", "acm"), "codes": ("programming", "data-structures", "algorithms", "coding-interview", "software-engineering", "exam-synthesis")},
    "database": {"label": "数据库与数据工程", "markers": ("数据库", "sql", "数据建模", "数据仓库", "事务", "索引"), "codes": ("programming", "data-structures", "databases", "advanced-database", "backend-service", "software-engineering", "project-delivery")},
    "network": {"label": "计算机网络", "markers": ("计算机网络", "tcp", "udp", "http", "协议", "网络工程"), "codes": ("cs-foundation", "networks", "network-practice", "operating-systems", "security", "distributed-systems", "exam-synthesis")},
    "security": {"label": "网络与应用安全", "markers": ("安全", "渗透", "漏洞", "密码学", "攻防", "认证授权"), "codes": ("networks", "operating-systems", "security", "auth-security", "cybersecurity", "software-engineering", "project-delivery")},
    "rag_agents": {"label": "大模型、RAG 与智能体", "markers": ("大模型", "llm", "rag", "智能体", "agent", "transformer", "知识库", "自然语言"), "codes": ("python-data", "math-ai", "ml", "deep-learning", "nlp", "evaluation", "rag-agents", "trustworthy-ai", "project-delivery")},
    "computer_vision": {"label": "计算机视觉", "markers": ("计算机视觉", "图像", "视觉", "cv", "目标检测", "分割"), "codes": ("python-data", "math-ai", "ml", "deep-learning", "computer-vision", "evaluation", "trustworthy-ai", "project-delivery")},
    "data_science": {"label": "数据科学", "markers": ("数据分析", "数据科学", "统计", "可视化", "预测分析"), "codes": ("python-data", "math-ai", "ml", "data-science", "databases", "evaluation", "project-delivery")},
}

FOCUS_ANCHORS = {
    "web_fullstack": "web-frontend",
    "systems": "concurrency",
    "algorithms": "coding-interview",
    "database": "advanced-database",
    "network": "network-practice",
    "security": "cybersecurity",
    "rag_agents": "rag-agents",
    "computer_vision": "computer-vision",
    "data_science": "data-science",
}

TARGET_DEPTH = {"foundation": 2, "intermediate": 3, "proficient": 4, "advanced": 5}
CURRENT_DEPTH = {"beginner": 0, "foundation": 1, "intermediate": 2, "advanced": 3}
DEPTH_LABELS = {
    1: "目标必备概念",
    2: "核心机制拆解",
    3: "方法与最小验证",
    4: "目标场景迁移",
    5: "综合优化与开放问题",
}

# These overrides keep high-frequency computer-science routes genuinely atomic.
# Other catalogue entries are decomposed from their structured descriptions.
MICRO_CONCEPT_OVERRIDES: dict[str, list[str]] = {
    "cs-foundation": ["二进制信息表示", "程序的装入与执行", "软硬件接口分层"],
    "programming": ["变量与值的状态变化", "分支和循环控制流", "函数参数与返回值"],
    "python-basics": ["Python 对象与基本类型", "控制流执行顺序", "函数作用域与模块导入"],
    "debugging": ["最小可复现样例", "异常栈与断点定位", "边界条件与回归测试"],
    "oop": ["对象状态与职责", "组合和继承的取舍", "接口契约与多态"],
    "data-structures": ["线性表的操作代价", "树的层次与遍历", "图的表示与搜索", "散列冲突处理"],
    "algorithms": ["渐近复杂度与增长率", "分治递归式", "动态规划状态转移", "贪心选择性质"],
    "coding-interview": ["题意到数据结构映射", "算法不变量", "复杂度证明", "限时编码与测试"],
    "architecture": ["指令周期", "流水线数据冒险", "缓存局部性", "存储层次性能"],
    "operating-systems": ["进程与线程状态", "临界区与互斥", "虚拟地址转换", "文件系统一致性"],
    "concurrency": ["竞态条件识别", "互斥量与条件变量", "死锁四条件", "并发正确性验证"],
    "networks": ["分层封装与解封装", "TCP 可靠传输", "拥塞控制窗口", "路由与应用层协议"],
    "network-practice": ["TCP/IP 抓包字段", "HTTP 请求响应链", "网络故障分层定位", "时延吞吐测量"],
    "databases": ["关系与函数依赖", "SQL 查询语义", "B+树索引", "事务隔离与恢复"],
    "advanced-database": ["执行计划解读", "复合索引选择", "并发控制异常", "缓存与容量边界"],
    "software-engineering": ["可验收需求", "模块边界与依赖", "测试层次", "版本与发布策略"],
    "web-frontend": ["DOM 与组件状态", "响应式数据流", "路由和表单交互", "可访问性反馈"],
    "backend-service": ["API 资源建模", "输入校验", "错误语义", "服务分层与可观测性"],
    "auth-security": ["身份认证状态", "授权策略", "会话与令牌", "常见 Web 攻击面"],
    "deployment": ["环境配置隔离", "容器镜像", "持续交付门禁", "监控与回滚"],
    "security": ["资产与威胁建模", "认证和授权边界", "密码学安全目标", "安全开发生命周期"],
    "cybersecurity": ["攻击面枚举", "漏洞成因", "安全测试证据", "修复验证与合规记录"],
    "math-ai": ["向量矩阵运算", "概率变量与分布", "梯度与链式法则", "优化目标与约束"],
    "python-data": ["数组形状与广播", "缺失值和异常值", "数据划分", "可复现实验环境"],
    "ml": ["任务与标签定义", "经验风险与泛化", "训练验证测试划分", "偏差方差诊断"],
    "deep-learning": ["神经元与前向传播", "反向传播梯度", "正则化", "训练稳定性诊断"],
    "nlp": ["文本表示", "注意力计算", "Transformer 信息流", "提示与检索增强"],
    "rag-agents": ["知识切分粒度", "召回与重排", "上下文组装", "引用约束", "工具调用评测"],
    "computer-vision": ["图像张量表示", "卷积感受野", "检测与分割输出", "视觉指标与误差"],
    "evaluation": ["指标与任务对齐", "基线和消融", "统计不确定性", "误差分层分析"],
    "trustworthy-ai": ["可靠性失效模式", "公平性度量", "隐私边界", "透明度与治理记录"],
    "data-science": ["变量类型与数据质量", "分布和异常值", "相关与因果边界", "可视化论证"],
    "project-delivery": ["成果验收条件", "可复现运行步骤", "测试证据", "演示与复盘"],
    "exam-synthesis": ["考点映射", "限时作答策略", "错因分类", "间隔复习"],
}


def model_row(model: Any) -> dict[str, Any]:
    data = {column.name: getattr(model, column.name) for column in model.__table__.columns}
    for key in list(data):
        if key.endswith("_json"):
            default: Any = [] if key in {"knowledge_base_ids_json", "prerequisites_json", "source_refs_json", "options_json", "citations_json", "recommendations_json"} else {}
            data[key.removesuffix("_json")] = loads(data[key], default)
    return data


class LearningSpaceService:
    model_row = staticmethod(model_row)

    async def access(self, db: AsyncSession, project_id: str, user: UserAccount) -> LearningProject:
        project = await db.get(LearningProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="学习方向不存在")
        if project.owner_id != user.id:
            raise HTTPException(status_code=403, detail="你无权访问该学习方向")
        return project

    async def subject_pack(self, db: AsyncSession) -> dict[str, Any]:
        group = await db.scalar(select(KnowledgeBaseGroup).where(KnowledgeBaseGroup.name == COMPUTER_PACK_GROUP))
        if not group:
            raise HTTPException(status_code=503, detail="计算机学科包尚未完成初始化，请重启本地服务")
        member_ids = select(KnowledgeBaseGroupMember.knowledge_base_id).where(KnowledgeBaseGroupMember.group_id == group.id)
        bases = (await db.scalars(select(KnowledgeBase).where(KnowledgeBase.id.in_(member_ids)).order_by(KnowledgeBase.name))).all()
        agents = (await db.scalars(select(AgentDefinition).where(AgentDefinition.slug.in_(list(AGENT_SLUGS.values()))).order_by(AgentDefinition.name))).all()
        workflows = (await db.scalars(select(Workflow).where(Workflow.name.in_(list(WORKFLOW_NAMES.values()))).order_by(Workflow.name))).all()
        return {
            "group": model_row(group),
            "knowledge_bases": [model_row(item) for item in bases],
            "agents": [model_row(item) for item in agents],
            "workflows": [model_row(item) for item in workflows],
        }

    def default_bindings(self, pack: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        agent_by_slug = {item["slug"]: item["id"] for item in pack["agents"]}
        workflow_by_name = {item["name"]: item["id"] for item in pack["workflows"]}
        return (
            {key: agent_by_slug.get(slug, "") for key, slug in AGENT_SLUGS.items()},
            {key: workflow_by_name.get(name, "") for key, name in WORKFLOW_NAMES.items()},
        )

    @staticmethod
    def direction_profile(project: LearningProject, track: str) -> dict[str, Any]:
        source_text = " ".join(
            filter(None, [project.name, project.description, project.target, project.discipline, track])
        ).lower()
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for key, rule in DIRECTION_RULES.items():
            score = sum(2 if marker.lower() in project.name.lower() else 1 for marker in rule["markers"] if marker.lower() in source_text)
            if score:
                scored.append((score, key, rule))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected_rules = scored[:2]
        focus_domains = [item[2]["label"] for item in selected_rules]
        keywords = [marker for _, _, rule in selected_rules for marker in rule["markers"] if marker.lower() in source_text][:10]
        type_outcome = {
            "exam": "完成考点覆盖、限时训练和错因闭环",
            "project": "完成可运行成果、测试证据、演示材料和复盘记录",
            "skill": "形成可迁移技能、练习样例和能力验证",
            "topic": "形成结构化知识脉络、关键论证和专题成果",
            "course": "完成知识学习、阶段练习和综合评测",
        }.get(project.project_type, "形成可验证的方向成果")
        target = project.target.strip() or type_outcome
        deadline_text = project.deadline.isoformat() if project.deadline else "未设置截止时间"
        raw_signature = "|".join([
            project.name, project.description, project.target, project.project_type, track,
            project.current_level, project.target_level, str(project.weekly_hours), deadline_text,
        ])
        return {
            "title": project.name,
            "track": track,
            "focus_domains": focus_domains or [track],
            "focus_keys": [item[1] for item in selected_rules],
            "keywords": keywords or [track, project.discipline],
            "target_outcomes": [target, type_outcome],
            "learning_strategy": f"从 {project.current_level} 提升至 {project.target_level}，每周投入 {project.weekly_hours:g} 小时；知识学习、方向实践、证据评测三线并行。",
            "generated_from": ["方向名称", "方向描述", "学习目标", "项目类型", "当前水平", "目标水平", "每周投入", "截止时间"],
            "signature": hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()[:16],
        }

    @staticmethod
    def select_direction_nodes(track: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
        catalogue: dict[str, dict[str, Any]] = {}
        for group in TRACK_NODES.values():
            for node in group:
                catalogue.setdefault(node["code"], node)
        for node in SPECIALIZED_NODES:
            catalogue[node["code"]] = node

        focus_keys = profile.get("focus_keys", [])
        if focus_keys:
            codes: list[str] = []
            for key in focus_keys:
                for code in DIRECTION_RULES[key]["codes"]:
                    if code not in codes:
                        codes.append(code)
            # Every focused direction retains a fundamentals anchor and a
            # verifiable final outcome, without expanding back into a generic curriculum.
            anchor = "python-basics" if track == "程序设计" else ("math-ai" if track == "人工智能" else "cs-foundation")
            if anchor in catalogue and anchor not in codes:
                codes.insert(0, anchor)
            if "project-delivery" not in codes and "exam-synthesis" not in codes:
                codes.append("project-delivery")
            codes = codes[:12]
            for key in focus_keys:
                anchor_code = FOCUS_ANCHORS.get(key)
                if anchor_code and anchor_code not in codes:
                    codes[-1] = anchor_code
        else:
            codes = [node["code"] for node in (TRACK_NODES.get(track) or TRACK_NODES["计算机基础"])]

        selected_codes = set(codes)
        selected: list[dict[str, Any]] = []
        for code in codes:
            source = catalogue.get(code)
            if not source:
                continue
            selected.append({**source, "prerequisites": [item for item in source["prerequisites"] if item in selected_codes]})
        return selected

    @staticmethod
    def learning_meta(node: LearningKnowledgeNode | dict[str, Any]) -> dict[str, Any]:
        refs = loads(node.source_refs_json, []) if isinstance(node, LearningKnowledgeNode) else node.get("source_refs", [])
        return next((item for item in refs if item.get("type") == "learning_path_metadata"), {})

    @staticmethod
    def _fallback_concepts(description: str) -> list[str]:
        parts = [
            re.sub(r"^(以及|并且|及|与)", "", item).strip(" 。")
            for item in re.split(r"[、，；;/]|(?:以及|并且)", description)
        ]
        return [item for item in parts if 2 <= len(item) <= 24][:4] or ["核心对象", "工作机制", "适用条件"]

    @classmethod
    def build_micro_nodes(cls, macros: list[dict[str, Any]], profile: dict[str, Any], target_level: str) -> list[dict[str, Any]]:
        target = (profile.get("target_outcomes") or ["形成可验证成果"])[0]
        max_depth = TARGET_DEPTH.get(target_level, 4)
        terminal_codes: dict[str, str] = {}
        by_parent: dict[str, list[dict[str, Any]]] = {}

        for macro in macros:
            concepts = MICRO_CONCEPT_OVERRIDES.get(macro["code"]) or cls._fallback_concepts(macro["description"])
            steps: list[tuple[str, str, int, str]] = [
                (
                    f"{macro['title']}：对象、术语与边界",
                    f"准确区分{macro['title']}中的研究对象、输入输出、核心术语和成立边界。",
                    1,
                    "用自己的话给出定义，并写出一个反例或不适用情形",
                )
            ]
            for index, concept in enumerate(concepts[:4]):
                depth = 2 if index < 2 else 3
                steps.append((
                    concept,
                    f"只聚焦小知识点“{concept}”：说明它的输入、状态变化、输出和判断依据。",
                    depth,
                    f"完成一个只检验“{concept}”的最小例题、代码片段或推导步骤",
                ))
            steps.append((
                f"{macro['title']}：最小验证",
                f"把前述小知识点组合成一个最小可运行、可计算或可判分的验证样例。",
                3,
                "提交输入、过程、输出、期望结果和失败样例",
            ))
            steps.append((
                f"{macro['title']}：目标场景迁移",
                f"把已验证机制迁移到当前目标“{target}”，比较直接套用与调整后的差异。",
                4,
                "完成一个直接服务当前目标的变式任务并说明迁移边界",
            ))
            steps.append((
                f"{macro['title']}：综合优化与开放问题",
                f"围绕目标“{target}”分析性能、可靠性、复杂度或可解释性取舍，并提出可验证改进。",
                5,
                "给出基线、改进假设、评价指标和反证条件",
            ))
            steps = [item for item in steps if item[2] <= max_depth]
            children: list[dict[str, Any]] = []
            for index, (title, description, depth, evidence) in enumerate(steps):
                code = macro["code"] if index == 0 else f"{macro['code']}--{index + 1:02d}"
                children.append({
                    "code": code,
                    "parent_code": macro["code"],
                    "title": title,
                    "domain": macro["domain"],
                    "description": description,
                    "depth_level": depth,
                    "depth_label": DEPTH_LABELS[depth],
                    "evidence": evidence,
                    "prerequisites": [],
                })
            by_parent[macro["code"]] = children
            terminal_codes[macro["code"]] = children[-1]["code"]

        result: list[dict[str, Any]] = []
        for macro in macros:
            children = by_parent[macro["code"]]
            external = [terminal_codes[code] for code in macro["prerequisites"] if code in terminal_codes]
            for index, child in enumerate(children):
                child["prerequisites"] = external if index == 0 else [children[index - 1]["code"]]
                result.append(child)
        return result

    async def scaffold(
        self,
        db: AsyncSession,
        project: LearningProject,
        track: str,
        preserved_mastery: dict[str, tuple[float, str]] | None = None,
    ) -> None:
        profile = self.direction_profile(project, track)
        macros = self.select_direction_nodes(track, profile)
        nodes = self.build_micro_nodes(macros, profile, project.target_level)
        refs = [
            {"title": "ACM/IEEE-CS Computing Curricula 2023", "source": "ACM/IEEE-CS", "url": "https://csed.acm.org/"},
            {"title": "计算机科学学科包", "source": "EvoAgent 本地知识库", "knowledge_group": COMPUTER_PACK_GROUP},
        ]
        target = profile["target_outcomes"][0]
        for index, item in enumerate(nodes):
            mastery_map = preserved_mastery or {}
            if item["code"] in mastery_map:
                mastery = round(float(mastery_map[item["code"]][0]), 1)
            else:
                parent_mastery = mastery_map.get(item["parent_code"], (0.0, "not_started"))
                mastery = round(min(float(parent_mastery[0]), 70.0), 1)
            state = "mastered" if mastery >= 80 else "learning" if mastery > 0 else "not_started"
            metadata = {
                "type": "learning_path_metadata",
                "granularity": "micro",
                "parent_code": item["parent_code"],
                "depth_level": item["depth_level"],
                "depth_label": item["depth_label"],
                "evidence_requirement": item["evidence"],
                "goal": target,
            }
            db.add(LearningKnowledgeNode(
                project_id=project.id,
                code=item["code"],
                title=item["title"],
                domain=item["domain"],
                description=f"面向“{project.name}”的当前目标“{target}”：{item['description']} 达标证据：{item['evidence']}。",
                prerequisites_json=dumps(item["prerequisites"]),
                source_refs_json=dumps([*refs, metadata]),
                order_index=index,
                mastery=mastery,
                status=state,
            ))
        previous = loads(project.settings_json, {})
        project.settings_json = dumps({
            **previous,
            "track": track,
            "plan_version": 3,
            "content_version": 3,
            "path_granularity": "micro_knowledge_point",
            "target_depth": TARGET_DEPTH.get(project.target_level, 4),
            "personalized": True,
            "direction_profile_stale": False,
            "usability_mode": "focused",
            "direction_profile": profile,
            "last_regenerated_at": datetime.now(timezone.utc).isoformat(),
        })
        await db.flush()
        await self.seed_questions(db, project)

    async def seed_questions(self, db: AsyncSession, project: LearningProject) -> list[LearningQuestion]:
        existing = await db.scalar(select(func.count(LearningQuestion.id)).where(LearningQuestion.project_id == project.id)) or 0
        if existing:
            return (await db.scalars(select(LearningQuestion).where(LearningQuestion.project_id == project.id))).all()
        nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id).order_by(LearningKnowledgeNode.order_index))).all()
        profile = loads(project.settings_json, {}).get("direction_profile", {})
        target = (profile.get("target_outcomes") or [project.target or "形成方向成果"])[0]
        questions: list[LearningQuestion] = []
        for index, node in enumerate(nodes):
            answer = f"能够解释{node.title}的核心机制，并将其用于“{project.name}”的目标“{target}”，提交可复现步骤、结果与验证证据"
            options = [
                answer,
                f"只记住{node.title}的术语，不需要说明适用边界",
                "复制一个无关示例，只要能够运行即可",
                "跳过验证，直接把结论写入最终成果",
            ]
            question = LearningQuestion(
                project_id=project.id,
                knowledge_node_id=node.id,
                question_type="single_choice",
                prompt=f"在“{project.name}”方向中学习【{node.title}】后，哪一项最符合当前方向的可验证学习要求？",
                options_json=dumps(options),
                answer_json=dumps({"value": answer}),
                rubric_json=dumps({"keywords": [node.title, project.name, "验证证据"], "full_score": 100, "direction_signature": profile.get("signature")}),
                difficulty=min(5, 1 + index % 4),
                source_refs_json=dumps(loads(node.source_refs_json, [])),
            )
            db.add(question)
            questions.append(question)
        await db.flush()
        return questions

    async def rebuild_direction(self, db: AsyncSession, project: LearningProject, *, track: str | None, keep_memories: bool) -> dict[str, Any]:
        current_track = track or loads(project.settings_json, {}).get("track") or "计算机基础"
        old_nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id))).all()
        preserved_mastery: dict[str, tuple[float, str]] = {}
        parent_buckets: dict[str, list[LearningKnowledgeNode]] = {}
        for node in old_nodes:
            preserved_mastery[node.code] = (node.mastery, node.status)
            parent = self.learning_meta(node).get("parent_code")
            if parent:
                parent_buckets.setdefault(parent, []).append(node)
        for parent, children in parent_buckets.items():
            preserved_mastery[parent] = (
                sum(item.mastery for item in children) / max(1, len(children)),
                "learning" if any(item.mastery > 0 for item in children) else "not_started",
            )
        # Explicit order is portable across SQLite and packaged desktop builds.
        for model in (LearningMistake, LearningAttempt, LearningQuestion, LearningTutorTurn, LearningTask, LearningAssessment, LearningKnowledgeNode):
            await db.execute(delete(model).where(model.project_id == project.id))
        if not keep_memories:
            await db.execute(delete(LearningMemory).where(LearningMemory.project_id == project.id))
        await db.flush()
        await self.scaffold(db, project, current_track, preserved_mastery=preserved_mastery)
        tasks = await self.generate_plan(db, project, regenerate=False, start_at=None, focus=[])
        return {"project": await self.project_payload(db, project), "tasks_generated": len(tasks), "memories_preserved": keep_memories}

    async def project_payload(self, db: AsyncSession, project: LearningProject) -> dict[str, Any]:
        data = model_row(project)
        counts = {}
        for name, model in {
            "nodes": LearningKnowledgeNode,
            "tasks": LearningTask,
            "questions": LearningQuestion,
            "attempts": LearningAttempt,
            "mistakes": LearningMistake,
            "memories": LearningMemory,
        }.items():
            counts[name] = await db.scalar(select(func.count(model.id)).where(model.project_id == project.id)) or 0
        completed = await db.scalar(select(func.count(LearningTask.id)).where(LearningTask.project_id == project.id, LearningTask.status == "completed")) or 0
        mastery = await db.scalar(select(func.avg(LearningKnowledgeNode.mastery)).where(LearningKnowledgeNode.project_id == project.id)) or 0
        data["counts"] = counts
        data["progress"] = round(100 * completed / max(1, counts["tasks"]))
        data["mastery"] = round(float(mastery), 1)
        data["role"] = "owner"
        return data

    async def workspace(self, db: AsyncSession, project: LearningProject) -> dict[str, Any]:
        settings = loads(project.settings_json, {})
        if int(settings.get("content_version", 0) or 0) < 3 or settings.get("direction_profile_stale"):
            # One-time migration for directions created by the former generic
            # scaffold. User-authored memories and prior mastery evidence are
            # retained; broad-node mastery is conservatively capped when it is
            # projected onto newly created micro knowledge points.
            await self.rebuild_direction(
                db,
                project,
                track=settings.get("track") or "计算机基础",
                keep_memories=True,
            )

        async def rows(model: Any, order: Any) -> list[dict[str, Any]]:
            items = (await db.scalars(select(model).where(model.project_id == project.id).order_by(order))).all()
            return [model_row(item) for item in items]
        return {
            "project": await self.project_payload(db, project),
            "nodes": await rows(LearningKnowledgeNode, LearningKnowledgeNode.order_index),
            "tasks": await rows(LearningTask, LearningTask.scheduled_for),
            "turns": await rows(LearningTutorTurn, LearningTutorTurn.created_at),
            "questions": await rows(LearningQuestion, LearningQuestion.created_at),
            "attempts": await rows(LearningAttempt, LearningAttempt.created_at),
            "mistakes": await rows(LearningMistake, LearningMistake.created_at),
            "memories": await rows(LearningMemory, LearningMemory.created_at),
            "assessments": await rows(LearningAssessment, LearningAssessment.created_at.desc()),
        }

    async def generate_plan(self, db: AsyncSession, project: LearningProject, *, regenerate: bool, start_at: datetime | None, focus: list[str]) -> list[LearningTask]:
        if regenerate:
            await db.execute(delete(LearningTask).where(LearningTask.project_id == project.id, LearningTask.source == "learning_plan"))
        existing = (await db.scalars(select(LearningTask).where(LearningTask.project_id == project.id).order_by(LearningTask.scheduled_for))).all()
        if existing and not regenerate:
            return existing
        nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id).order_by(LearningKnowledgeNode.order_index))).all()
        if focus:
            selected = [node for node in nodes if node.id in focus or node.code in focus or node.domain in focus]
            nodes = selected or nodes
        else:
            initial_depth = min(
                TARGET_DEPTH.get(project.target_level, 4),
                max(1, CURRENT_DEPTH.get(project.current_level, 0) + 1),
            )
            active_nodes = [
                node for node in nodes
                if int(self.learning_meta(node).get("depth_level", 1)) <= initial_depth and node.mastery < 80
            ]
            # The plan is a rolling window over atomic points, not a dump of
            # the entire curriculum. Replanning advances this window as the
            # learner demonstrates mastery and deeper points unlock.
            nodes = (active_nodes or nodes)[:12]
        start = start_at or datetime.now(timezone.utc)
        sessions_per_week = max(2, min(10, round(project.weekly_hours * 60 / 45)))
        profile = loads(project.settings_json, {}).get("direction_profile", {})
        target = (profile.get("target_outcomes") or [project.target or "形成方向成果"])[0]
        practice_deliverable = {
            "exam": "完成一道与本节点对应的限时题，记录考点、得分、耗时和错因",
            "project": "把本节点用于方向原型，提交实现片段、测试结果和设计说明",
            "skill": "完成一个可复现练习，记录输入、步骤、输出和适用边界",
            "topic": "形成一段带来源的专题论证，并给出反例或边界条件",
            "course": "完成节点练习并用自己的话解释机制、条件和常见误区",
        }.get(project.project_type, "完成一个可检查的方向练习")
        tasks: list[LearningTask] = []
        session = 0
        for node in nodes:
            for module, prefix, duration in (("learn", "学习", 45), ("practice", "练习", 35), ("review", "复习", 25)):
                week, day_slot = divmod(session, sessions_per_week)
                scheduled = start + timedelta(days=week * 7 + round(day_slot * 6 / max(1, sessions_per_week - 1)))
                task = LearningTask(
                    project_id=project.id,
                    knowledge_node_id=node.id,
                    module=module,
                    title=f"{project.name}｜{prefix}：{node.title}",
                    description=(
                        f"围绕“{project.name}”的目标“{target}”完成{node.title}。"
                        + (practice_deliverable if module == "practice" else (
                            "阅读学科包来源，建立概念—机制—适用条件—方向用途四层笔记。" if module == "learn"
                            else "不看资料复述关键机制，回查不确定项，并安排下一次间隔复习。"
                        ))
                    ),
                    scheduled_for=scheduled,
                    duration_minutes=duration,
                    priority=4 if module == "learn" else 3,
                )
                db.add(task)
                tasks.append(task)
                session += 1
        project.stage = "learning"
        await db.flush()
        return tasks

    async def retrieve(self, db: AsyncSession, project: LearningProject, query: str, limit: int = 4) -> list[dict[str, Any]]:
        base_ids = loads(project.knowledge_base_ids_json, [])
        if not base_ids:
            return []
        chunks = (await db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id.in_(base_ids), KnowledgeChunk.level == "child").limit(200))).all()
        lowered = query.lower()
        terms = set(re.findall(r"[a-z0-9_+.#-]{2,}", lowered))
        chinese = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
        terms.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
        semantic_terms = {
            "复杂度 算法 增长 输入规模 渐近": ("复杂度", "o(", "算法", "log n", "排序", "查找"),
            "操作系统 进程 并发 互斥 临界区": ("进程", "线程", "并发", "互斥", "虚拟内存", "操作系统"),
            "网络 协议 可靠 传输": ("tcp", "udp", "网络", "http", "协议", "路由"),
            "数据库 事务 索引": ("数据库", "事务", "sql", "索引", "acid"),
            "机器学习 模型 泛化 评测": ("机器学习", "模型", "训练集", "测试集", "泛化"),
            "检索 增强 引用 来源": ("rag", "检索增强", "引用", "来源", "召回"),
            "python 调试 异常 程序": ("python", "异常", "调试", "函数", "变量"),
        }
        for expansion, markers in semantic_terms.items():
            if any(marker in lowered for marker in markers):
                terms.update(expansion.split())
        scored = []
        for chunk in chunks:
            haystack = f"{chunk.title} {chunk.content}".lower()
            score = sum(4 if term in chunk.title.lower() else 1 for term in terms if term in haystack)
            if score:
                scored.append((score, chunk))
        if not scored:
            scored = [(1, chunk) for chunk in chunks[:limit]]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{"id": chunk.id, "title": chunk.title, "source": chunk.citation or "计算机科学学科包", "excerpt": chunk.content[:420], "score": score} for score, chunk in scored[:limit]]

    async def tutor(self, db: AsyncSession, project: LearningProject, user: UserAccount, *, message: str, mode: str, knowledge_node_id: str | None, agent_id: str | None) -> LearningTutorTurn:
        bindings = loads(project.agent_bindings_json, {})
        selected_agent_id = agent_id or bindings.get("tutor") or None
        user_turn = LearningTutorTurn(project_id=project.id, knowledge_node_id=knowledge_node_id, agent_id=selected_agent_id, role="user", mode=mode, content=message)
        db.add(user_turn)
        await db.flush()
        node = await db.get(LearningKnowledgeNode, knowledge_node_id) if knowledge_node_id else None
        profile = loads(project.settings_json, {}).get("direction_profile", {})
        lowered_message = message.lower()
        if mode == "debug" or any(marker in lowered_message for marker in ("报错", "异常", "error", "bug", "代码", "traceback")):
            question_type = "code_debugging"
        elif any(marker in lowered_message for marker in ("推导", "公式", "证明", "为什么等于", "复杂度")):
            question_type = "formula_derivation"
        elif any(marker in lowered_message for marker in ("区别", "辨析", "对比", "不同", "vs", "versus")):
            question_type = "concept_comparison"
        else:
            question_type = "concept_explanation"
        direction_query = " ".join(filter(None, [project.name, project.target, node.title if node else "", message]))
        citations = await self.retrieve(db, project, direction_query)
        agent = await db.get(AgentDefinition, selected_agent_id) if selected_agent_id else None
        answer = ""
        if agent and agent.provider != "demo":
            try:
                history_rows = (await db.scalars(select(LearningTutorTurn).where(LearningTutorTurn.project_id == project.id).order_by(LearningTutorTurn.created_at.desc()).limit(10))).all()
                history = [{"role": item.role, "content": item.content} for item in reversed(history_rows)]
                evidence = "\n\n".join(f"[{i + 1}] {item['title']}：{item['excerpt']}" for i, item in enumerate(citations))
                prompt = f"当前学习方向：{project.name}\n方向画像：{dumps(profile)}\n方向目标：{project.target or '形成可验证的方向成果'}\n学习主题：{node.title if node else project.name}\n辅导模式：{mode}\n问题类型：{question_type}\n学生问题：{message}\n可引用资料：\n{evidence}\n回答必须始终落在当前方向，不得输出可直接复用于任意方向的空泛建议；采用“问题澄清—前置知识—分步讲解—验证/反例—迁移练习”的可检查结构。公式需说明每个符号和推导条件；代码需给最小复现、定位假设和回归测试；概念辨析需列出共同点、关键差异和适用边界。明确区分资料事实与推理，并在依据处标注[1]等来源编号。"
                run = await agent_engine.run(db, agent.id, prompt, {"user_id": user.id}, conversation_messages=history)
                answer = run.output_text
            except Exception:
                answer = ""
        if not answer:
            topic = node.title if node else project.name
            target = project.target or "形成可验证的方向成果"
            evidence_hint = citations[0]["excerpt"][:180] if citations else "当前问题可先从定义、输入输出和边界条件三个方面拆解。"
            if question_type == "formula_derivation":
                answer = f"我们把“{topic}”的推导拆成五步：①写明已知量、未知量和符号定义；②列出成立所需的假设与边界；③从定义或基本定理逐式变换，每一步注明依据；④用量纲、边界值或小规模样例检验；⑤说明它如何服务于“{project.name}”的目标“{target}”。\n\n资料起点：{evidence_hint}\n\n先请你补充具体公式或希望证明的等式；下一步可检查产物是一份符号表和第一步等价变换，我会逐步核对，而不是跳步给结论。"
            elif question_type == "concept_comparison":
                answer = f"辨析“{topic}”时，先固定比较维度：定义对象、输入输出、核心机制、复杂度/代价、适用边界和失败情形。对“{project.name}”最重要的不是术语表面差异，而是它们会怎样改变目标“{target}”的实现与验证。\n\n资料起点：{evidence_hint}\n\n下一步可检查产物：请给出要比较的两个概念和一个当前方向中的具体场景，我将逐维度判断，并给出反例避免混淆。"
            elif mode == "socratic":
                answer = f"在你的方向“{project.name}”中，{topic}需要直接服务于目标“{target}”。我们先不直接给结论：请你分别写出这个方向场景的输入、期望输出、约束和验证指标。\n\n资料提示：{evidence_hint}\n\n下一步可检查产物：一份包含上述四项的方向问题定义。你写出第一版后，我会继续追问并检查推理链。"
            elif mode == "debug":
                answer = f"针对“{project.name}”中的{topic}，请先提供最小可复现输入、期望结果、实际结果和错误信息，再按“复现—定位—提出假设—最小修改—回归测试”推进。\n\n依据提示：{evidence_hint}\n\n下一步可检查产物：最小复现样例与至少一个回归测试。"
            elif mode == "feynman":
                answer = f"请用三句话解释“{topic}”如何用于你的方向“{project.name}”：它解决什么具体问题、核心机制是什么、在哪种边界下会失败。\n\n可作为起点：{evidence_hint}\n\n下一步可检查产物：三句话说明与一个方向反例。"
            else:
                answer = f"在“{project.name}”中，“{topic}”可按概念定义、工作机制、适用条件、方向应用和常见误区五层理解。\n\n依据提示：{evidence_hint}\n\n下一步可检查产物：请结合目标“{target}”复述核心机制并给出一个具体应用点，我再帮助你校正。"
        assistant = LearningTutorTurn(project_id=project.id, knowledge_node_id=knowledge_node_id, agent_id=selected_agent_id, role="assistant", mode=mode, content=answer, citations_json=dumps(citations), metadata_json=dumps({"source_traceable": True, "agent_name": agent.name if agent else "方向自适应辅导 Agent", "direction_signature": profile.get("signature"), "direction_name": project.name, "question_type": question_type, "guidance_protocol": ["问题澄清", "前置知识", "分步讲解", "验证或反例", "迁移练习"]}))
        db.add(assistant)
        await db.flush()
        return assistant

    @staticmethod
    def grade(question: LearningQuestion, submitted: Any) -> tuple[float, bool, str, dict[str, Any]]:
        expected = loads(question.answer_json, {})
        rubric = loads(question.rubric_json, {})
        value = submitted.get("value") if isinstance(submitted, dict) else submitted
        target = expected.get("value", expected)
        if question.question_type in {"single_choice", "true_false", "fill"}:
            correct = str(value).strip().lower() == str(target).strip().lower()
            score = 100.0 if correct else 0.0
            result = {"exact_match": correct, "expected": target, "submitted": value}
        elif question.question_type == "multiple_choice":
            left = {str(item).strip().lower() for item in (value or [])}
            right = {str(item).strip().lower() for item in (target or [])}
            overlap = len(left & right)
            score = round(100 * overlap / max(1, len(right)) - 25 * len(left - right), 1)
            score = max(0.0, score)
            correct = left == right
            result = {"matched": overlap, "required": len(right), "extra": len(left - right)}
        else:
            text = str(value or "").lower()
            keywords = [str(item).lower() for item in rubric.get("keywords", [])]
            matched = [item for item in keywords if item in text]
            score = round(100 * len(matched) / max(1, len(keywords)), 1)
            correct = score >= 80
            result = {"matched_keywords": matched, "required_keywords": keywords}
        feedback = "回答正确。请尝试说明为什么其他选项不成立，以巩固迁移能力。" if correct else f"本次得分 {score:.0f}。标准要点：{target}。建议回到概念定义和适用条件，再完成一次间隔复习。"
        return score, correct, feedback, result

    async def submit_attempt(self, db: AsyncSession, project: LearningProject, question: LearningQuestion, answer: Any, agent_id: str | None) -> tuple[LearningAttempt, LearningMistake | None]:
        score, correct, feedback, result = self.grade(question, answer)
        attempt = LearningAttempt(project_id=project.id, question_id=question.id, agent_id=agent_id, answer_json=dumps(answer), score=score, is_correct=correct, feedback=feedback, error_type="" if correct else "concept_or_boundary", rubric_result_json=dumps(result))
        db.add(attempt)
        await db.flush()
        node = await db.get(LearningKnowledgeNode, question.knowledge_node_id) if question.knowledge_node_id else None
        if node:
            node.mastery = round(node.mastery * 0.7 + score * 0.3, 1)
            node.status = "mastered" if node.mastery >= 80 else "learning"
        mistake = None
        if score < 80:
            mistake = LearningMistake(project_id=project.id, attempt_id=attempt.id, knowledge_node_id=question.knowledge_node_id, cause="概念定义、边界条件或选项辨析尚未稳定。", correction=f"对照标准要点复述并完成同类题：{loads(question.answer_json, {}).get('value', '')}", next_review_at=datetime.now(timezone.utc) + timedelta(days=1))
            db.add(mistake)
        await db.flush()
        return attempt, mistake

    async def assess(self, db: AsyncSession, project: LearningProject, period: str) -> LearningAssessment:
        tasks = (await db.scalars(select(LearningTask).where(LearningTask.project_id == project.id))).all()
        attempts = (await db.scalars(select(LearningAttempt).where(LearningAttempt.project_id == project.id))).all()
        nodes = (await db.scalars(select(LearningKnowledgeNode).where(LearningKnowledgeNode.project_id == project.id))).all()
        mistakes = (await db.scalars(select(LearningMistake).where(LearningMistake.project_id == project.id))).all()
        task_completion = 100 * sum(item.status == "completed" for item in tasks) / max(1, len(tasks))
        accuracy = sum(item.score for item in attempts) / max(1, len(attempts))
        mastery = sum(item.mastery for item in nodes) / max(1, len(nodes))
        correction = 100 * sum(item.status == "mastered" for item in mistakes) / max(1, len(mistakes)) if mistakes else 100
        coverage = 100 * sum(item.status != "not_started" for item in nodes) / max(1, len(nodes))
        overall = 0.25 * task_completion + 0.30 * accuracy + 0.25 * mastery + 0.10 * correction + 0.10 * coverage
        metrics = {"task_completion": round(task_completion, 1), "practice_accuracy": round(accuracy, 1), "knowledge_mastery": round(mastery, 1), "mistake_correction": round(correction, 1), "knowledge_coverage": round(coverage, 1), "attempt_count": len(attempts), "completed_tasks": sum(item.status == "completed" for item in tasks), "total_tasks": len(tasks)}
        weak_nodes = sorted(nodes, key=lambda item: item.mastery)[:3]
        weak_names = "、".join(item.title for item in weak_nodes) or "当前方向节点"
        target = project.target or "形成可验证的方向成果"
        recommendations = []
        if task_completion < 70: recommendations.append(f"围绕“{project.name}”优先完成已排期任务，先收敛到目标“{target}”，避免同时开启过多节点。")
        if accuracy < 80: recommendations.append(f"针对{weak_names}，练习后立即记录错因，并于 1、3、7 天完成同方向变式题。")
        if mastery < 70: recommendations.append(f"使用苏格拉底辅导复述{weak_names}如何服务于“{project.name}”，再提交方向应用证据检验迁移。")
        if not recommendations: recommendations.append(f"“{project.name}”当前节奏稳定，可增加跨节点综合实践，并以“{target}”为验收标准保持每周评测。")
        item = LearningAssessment(project_id=project.id, period=period, overall_score=round(overall, 1), metrics_json=dumps(metrics), summary=f"“{project.name}”方向综合学习指数 {overall:.1f}/100。该分数由任务完成度、方向练习正确率、知识掌握度、错题订正率和方向知识覆盖度加权得到；当前优先关注：{weak_names}。", recommendations_json=dumps(recommendations))
        db.add(item)
        await db.flush()
        return item


learning_space_service = LearningSpaceService()
