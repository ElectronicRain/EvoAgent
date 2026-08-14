from __future__ import annotations

"""Authoritative computer-science curriculum ontology used by Learning Space.

The catalogue follows the 166 knowledge units in ACM/IEEE-CS/AAAI CS2023.
Networking is expanded one level further because a useful networking learning
path needs protocol/mechanism-level leaves rather than only eight unit labels.
"""

from typing import Any


ONTOLOGY_VERSION = "cs2023-v1"

ONTOLOGY_SOURCES = [
    {
        "title": "Computer Science Curricula 2023",
        "source": "ACM / IEEE Computer Society / AAAI",
        "url": "https://csed.acm.org/wp-content/uploads/2024/04/3.1-Body-of-Knowledge-1.pdf",
    },
    {
        "title": "CS2023 Knowledge Areas",
        "source": "ACM / IEEE Computer Society / AAAI",
        "url": "https://csed.acm.org/knowledge-areas/",
    },
]

NETWORK_PROTOCOL_SOURCES = [
    {"title": "RFC 1122: Requirements for Internet Hosts — Communication Layers", "source": "IETF", "url": "https://datatracker.ietf.org/doc/rfc1122/"},
    {"title": "RFC 9293: Transmission Control Protocol (TCP)", "source": "IETF", "url": "https://datatracker.ietf.org/doc/rfc9293/"},
    {"title": "RFC 9110: HTTP Semantics", "source": "IETF", "url": "https://datatracker.ietf.org/doc/rfc9110/"},
]

AREA_TITLES = {
    "AI": "人工智能",
    "AL": "算法基础",
    "AR": "体系结构与组成",
    "DM": "数据管理",
    "FPL": "程序设计语言基础",
    "GIT": "图形学与交互技术",
    "HCI": "人机交互",
    "MSF": "数学与统计基础",
    "NC": "网络与通信",
    "OS": "操作系统",
    "PDC": "并行与分布式计算",
    "SDF": "软件开发基础",
    "SE": "软件工程",
    "SEC": "计算机安全",
    "SEP": "社会、伦理与职业规范",
    "SF": "系统基础",
    "SPD": "专用平台开发",
}

# code|Chinese display title.  These are the complete CS2023 knowledge-unit
# headings, translated for the Chinese UI while retaining the official code.
_UNITS_RAW: dict[str, str] = {
    "AI": """AI-Introduction|人工智能基本问题
AI-Search|搜索
AI-KRR|知识表示与推理基础
AI-ML|机器学习
AI-SEP|人工智能应用与社会影响
AI-LRR|逻辑表示与推理
AI-Probability|概率表示与推理
AI-Planning|规划
AI-Agents|智能体与认知系统
AI-NLP|自然语言处理
AI-Robotics|机器人学
AI-Vision|感知与计算机视觉""",
    "AL": """AL-Foundational|基础数据结构与算法
AL-Strategies|算法设计策略
AL-Complexity|计算复杂性
AL-Models|计算模型与形式语言
AL-SEP|算法的社会、伦理与职业问题""",
    "AR": """AR-Logic|数字逻辑与数字系统
AR-Representation|机器级数据表示
AR-Assembly|汇编级机器组织
AR-Memory|存储层次
AR-IO|接口与通信
AR-Organization|处理器功能组织
AR-Performance-Energy|性能与能效
AR-Heterogeneity|异构体系结构
AR-Security|安全处理器体系结构
AR-Quantum|量子体系结构
AR-SEP|体系结构可持续性问题""",
    "DM": """DM-Data|数据的作用与数据生命周期
DM-Core|数据库系统核心概念
DM-Modeling|数据建模
DM-Relational|关系数据库
DM-Querying|查询构造
DM-Processing|查询处理
DM-Internals|数据库管理系统内部机制
DM-NoSQL|NoSQL 系统
DM-Security|数据安全与隐私
DM-Analytics|数据分析
DM-Distributed|分布式数据库与云计算
DM-Unstructured|半结构化与非结构化数据库
DM-SEP|数据管理的社会、伦理与职业问题""",
    "FPL": """FPL-OOP|面向对象程序设计
FPL-Functional|函数式程序设计
FPL-Logic|逻辑程序设计
FPL-Scripting|Shell 脚本
FPL-Event-Driven|事件驱动与响应式程序设计
FPL-Parallel|并行与分布式程序设计
FPL-Aspect|面向切面程序设计
FPL-Types|类型系统
FPL-Systems|系统执行与内存模型
FPL-Translation|语言翻译与执行
FPL-Abstraction|程序抽象与表示
FPL-Syntax|语法分析
FPL-Semantics|编译器语义分析
FPL-Analysis|程序分析与分析器
FPL-Code|代码生成
FPL-Run-Time|运行时行为与系统
FPL-Constructs|高级程序设计构造
FPL-Pragmatics|程序语言语用学
FPL-Formalism|形式语义
FPL-Methodologies|形式化开发方法
FPL-Design|程序设计语言设计原则
FPL-SEP|程序语言的社会、伦理与职业问题""",
    "GIT": """GIT-Fundamentals|计算机图形学基本概念
GIT-Visualization|可视化
GIT-Rendering|应用渲染与技术
GIT-Modeling|几何建模
GIT-Shading|着色与高级渲染
GIT-Animation|计算机动画
GIT-Simulation|仿真
GIT-Immersion|沉浸式技术
GIT-Interaction|图形交互
GIT-Image|图像处理
GIT-Physical|实体与物理计算
GIT-SEP|图形技术的社会、伦理与职业问题""",
    "HCI": """HCI-User|理解用户、个人目标与社会交互
HCI-Accountability|设计中的问责与责任
HCI-Accessibility|无障碍与包容性设计
HCI-Evaluation|设计评估
HCI-Design|交互系统设计
HCI-SEP|人机交互的社会、伦理与职业问题""",
    "MSF": """MSF-Discrete|离散数学
MSF-Probability|概率论
MSF-Statistics|统计学
MSF-Linear|线性代数
MSF-Calculus|微积分""",
    "NC": """NC-Fundamentals|网络与通信基础
NC-Applications|网络应用
NC-Reliability|可靠性支持
NC-Routing|路由与转发
NC-SingleHop|单跳通信
NC-Security|网络安全
NC-Mobility|移动性
NC-Emerging|网络前沿专题""",
    "OS": """OS-Purpose|操作系统的作用与目标
OS-Principles|操作系统基本原理
OS-Concurrency|并发
OS-Protection|保护与安全
OS-Scheduling|调度
OS-Process|进程模型
OS-Memory|内存管理
OS-Devices|设备管理
OS-Files|文件系统接口与实现
OS-Advanced-Files|高级文件系统
OS-Virtualization|虚拟化
OS-Real-time|实时与嵌入式系统
OS-Faults|容错
OS-SEP|操作系统的社会、伦理与职业问题""",
    "PDC": """PDC-Programs|并行与分布式程序
PDC-Communication|通信
PDC-Coordination|协调与同步
PDC-Evaluation|并行与分布式系统评测
PDC-Algorithms|并行与分布式算法""",
    "SDF": """SDF-Fundamentals|程序设计基本概念与实践
SDF-Data-Structures|基础数据结构
SDF-Algorithms|基础算法
SDF-Practices|软件开发实践
SDF-SEP|软件开发的社会、伦理与职业问题""",
    "SE": """SE-Teamwork|软件团队协作
SE-Tools|软件工具与开发环境
SE-Requirements|产品需求
SE-Design|软件设计
SE-Construction|软件构造
SE-Validation|软件验证与确认
SE-Refactoring|重构与代码演化
SE-Reliability|软件可靠性
SE-Formal|形式化方法""",
    "SEC": """SEC-Foundations|安全基础
SEC-SEP|安全的社会、伦理与职业问题
SEC-Coding|安全编码
SEC-Crypto|密码学
SEC-Engineering|安全分析、设计与工程
SEC-Forensics|数字取证
SEC-Governance|安全治理""",
    "SEP": """SEP-Context|计算的社会语境
SEP-Ethical-Analysis|伦理分析方法
SEP-Professional-Ethics|职业伦理
SEP-IP|知识产权
SEP-Privacy|隐私与公民自由
SEP-Communication|专业沟通
SEP-Sustainability|可持续性
SEP-History|计算史
SEP-Economies|计算经济学
SEP-Security|安全政策、法律与计算机犯罪
SEP-DEIA|多样性、公平、包容与无障碍""",
    "SF": """SF-Overview|计算机系统概览
SF-Foundations|系统基本概念
SF-Resource|资源管理
SF-Performance|系统性能
SF-Evaluation|性能评测
SF-Reliability|系统可靠性
SF-Security|系统安全
SF-Design|系统设计
SF-SEP|系统的社会、伦理与职业问题""",
    "SPD": """SPD-Common|专用平台的共性问题
SPD-Web|Web 平台
SPD-Mobile|移动平台
SPD-Robot|机器人平台
SPD-Embedded|嵌入式平台
SPD-Game|游戏平台
SPD-Interactive|交互计算平台
SPD-SEP-Mobile|移动平台的社会与伦理问题
SPD-SEP-Web|Web 平台的社会与伦理问题
SPD-SEP-Game|游戏平台的社会与伦理问题
SPD-SEP-Robotics|机器人平台的社会与伦理问题
SPD-SEP-Interactive|交互平台的社会与伦理问题""",
}

CS2023_UNITS: dict[str, list[tuple[str, str]]] = {
    area: [tuple(line.split("|", 1)) for line in raw.splitlines() if line.strip()]
    for area, raw in _UNITS_RAW.items()
}

# CS2023 NC topics, expanded to leaf nodes.  Titles are genuine networking
# concepts; assessment/transfer instructions are stored separately as evidence.
NETWORK_TOPICS: dict[str, list[str]] = {
    "NC-Fundamentals": [
        "计算机网络的作用与挑战", "互联网组织：ISP、自治系统与内容网络", "电路交换与分组交换",
        "TCP/IP 分层与各层职责", "封装、解封装与沙漏模型", "主机、路由器、交换机与接入点", "排队、时延、拥塞与服务水平",
    ],
    "NC-Applications": [
        "命名、DNS 与统一资源标识符", "客户端/服务器与对等网络", "云、边缘与雾计算应用范式",
        "应用的时延、带宽与丢包容忍需求", "HTTP 等应用层协议", "Socket API 与 TCP/UDP 交互",
    ],
    "NC-Reliability": [
        "UDP 与不可靠数据报服务", "可靠交付：丢失、重复与乱序", "差错检测、纠错与重传",
        "停止等待与滑动窗口流量控制", "拥塞检测与拥塞控制", "TCP 状态、可靠传输与性能",
    ],
    "NC-Routing": [
        "IP 地址、子网与 CIDR", "路由层次：域内与域间", "集中式、分布式与源路由",
        "转发表与最长前缀匹配", "NAT、IPv4/IPv6 与可扩展性", "BGP 与自治系统间路由",
    ],
    "NC-SingleHop": [
        "传输介质、带宽与调制", "编码与成帧", "随机接入与调度式介质访问控制",
        "以太网与交换式局域网", "IEEE 802.11 Wi-Fi", "生成树、VLAN 与局域网拓扑",
    ],
    "NC-Security": [
        "网络威胁、脆弱性与对策", "拒绝服务、欺骗、嗅探与中间人攻击", "TLS 与安全信道",
        "VPN、DMZ、零信任与网络隔离", "防火墙、入侵检测与网络监测", "安全 DNS、RPKI 与安全路由",
    ],
    "NC-Mobility": [
        "蜂窝网络与 4G/5G 基本机制", "802.11 移动漫游", "设备到设备与物联网通信", "自组织、多跳、机会与延迟容忍网络",
    ],
    "NC-Emerging": [
        "中间盒、负载均衡、NAT 与 CDN", "软件定义网络与网络虚拟化", "数据中心网络",
        "卫星、毫米波与可见光通信", "量子网络与量子互联网", "意图驱动网络与网络智能化",
    ],
}

NETWORK_UNIT_DEPENDENCIES = {
    "NC-Fundamentals": [],
    "NC-Applications": ["NC-Fundamentals"],
    "NC-Reliability": ["NC-Fundamentals", "NC-Applications"],
    "NC-Routing": ["NC-Fundamentals"],
    "NC-SingleHop": ["NC-Fundamentals"],
    "NC-Security": ["NC-Applications", "NC-Routing"],
    "NC-Mobility": ["NC-SingleHop", "NC-Routing"],
    "NC-Emerging": ["NC-Routing", "NC-Security", "NC-Mobility"],
}

FOCUS_AREAS: dict[str, tuple[str, ...]] = {
    "network": ("NC",),
    "operating_system": ("OS",),
    "architecture": ("AR",),
    "database": ("DM",),
    "algorithms": ("SDF", "AL"),
    "programming_languages": ("SDF", "FPL"),
    "software_engineering": ("SDF", "SE"),
    "web_fullstack": ("SDF", "SE", "HCI", "SPD"),
    "distributed": ("PDC",),
    "security": ("SEC",),
    "rag_agents": ("MSF", "AI"),
    "computer_vision": ("MSF", "AI", "GIT"),
    "graphics_hci": ("GIT", "HCI"),
    "data_science": ("MSF", "DM", "AI"),
    "systems": ("SF", "AR", "OS"),
}

# Some specializations use only a coherent subset of an area.  This prevents
# a computer-vision goal from pulling in NLP/robotics, or a Web goal from
# pulling in game/mobile platform nodes merely because they share an area.
FOCUS_UNITS: dict[str, set[str]] = {
    "web_fullstack": {
        "SDF-Fundamentals", "SDF-Data-Structures", "SDF-Algorithms", "SDF-Practices", "SDF-SEP",
        "SE-Teamwork", "SE-Tools", "SE-Requirements", "SE-Design", "SE-Construction", "SE-Validation", "SE-Refactoring", "SE-Reliability",
        "HCI-User", "HCI-Accountability", "HCI-Accessibility", "HCI-Evaluation", "HCI-Design", "HCI-SEP",
        "SPD-Common", "SPD-Web", "SPD-SEP-Web",
    },
    "rag_agents": {
        "MSF-Discrete", "MSF-Probability", "MSF-Statistics", "MSF-Linear",
        "AI-Introduction", "AI-Search", "AI-KRR", "AI-ML", "AI-SEP", "AI-Probability", "AI-Planning", "AI-Agents", "AI-NLP",
    },
    "computer_vision": {
        "MSF-Probability", "MSF-Statistics", "MSF-Linear", "MSF-Calculus",
        "AI-Introduction", "AI-ML", "AI-SEP", "AI-Vision",
        "GIT-Fundamentals", "GIT-Visualization", "GIT-Rendering", "GIT-Image",
    },
    "graphics_hci": {
        "GIT-Fundamentals", "GIT-Visualization", "GIT-Rendering", "GIT-Modeling", "GIT-Shading", "GIT-Animation", "GIT-Simulation", "GIT-Immersion", "GIT-Interaction", "GIT-Image", "GIT-Physical", "GIT-SEP",
        "HCI-User", "HCI-Accountability", "HCI-Accessibility", "HCI-Evaluation", "HCI-Design", "HCI-SEP",
    },
    "data_science": {
        "MSF-Probability", "MSF-Statistics", "MSF-Linear", "MSF-Calculus",
        "DM-Data", "DM-Core", "DM-Modeling", "DM-Relational", "DM-Querying", "DM-Security", "DM-Analytics", "DM-Distributed", "DM-Unstructured", "DM-SEP",
        "AI-Introduction", "AI-ML", "AI-SEP", "AI-Probability",
    },
}

TRACK_AREAS: dict[str, tuple[str, ...]] = {
    "计算机基础": ("SF", "SDF", "AL", "AR", "OS", "NC", "DM", "SE", "SEC", "AI", "SEP"),
    "程序设计": ("SDF", "AL", "FPL", "SE", "HCI", "SPD"),
    "人工智能": ("MSF", "DM", "AI", "SEC", "SEP"),
}


def _normalized_code(code: str) -> str:
    return code.lower().replace("/", "-").replace(" ", "")


def _depth(index: int, count: int) -> int:
    return min(5, 1 + (index * 5 // max(1, count)))


def _unit_nodes(areas: tuple[str, ...], allowed_units: set[str] | None = None) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for area in areas:
        units = [item for item in CS2023_UNITS[area] if allowed_units is None or item[0] in allowed_units]
        previous = ""
        for index, (official_code, title) in enumerate(units):
            code = _normalized_code(official_code)
            depth = _depth(index, len(units))
            nodes.append({
                "code": code,
                "parent_code": area.lower(),
                "knowledge_area": area,
                "knowledge_unit": official_code,
                "title": title,
                "domain": AREA_TITLES[area],
                "description": f"CS2023 {official_code} 知识单元：{title}。掌握其核心概念、机制、适用条件及与相邻知识单元的关系。",
                "depth_level": depth,
                "evidence": f"解释“{title}”的关键机制，并完成一个可检查的例题、设计或实现证据",
                "prerequisites": [previous] if previous else [],
                "source_refs": ONTOLOGY_SOURCES,
            })
            previous = code
    return nodes


def _network_nodes() -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    terminal: dict[str, str] = {}
    unit_titles = dict(CS2023_UNITS["NC"])
    unit_depth = {
        "NC-Fundamentals": 1,
        "NC-Applications": 2,
        "NC-Reliability": 3,
        "NC-Routing": 3,
        "NC-SingleHop": 2,
        "NC-Security": 4,
        "NC-Mobility": 4,
        "NC-Emerging": 5,
    }
    for unit_code, topics in NETWORK_TOPICS.items():
        previous = ""
        external = [terminal[item] for item in NETWORK_UNIT_DEPENDENCIES[unit_code] if item in terminal]
        for index, title in enumerate(topics, start=1):
            code = f"{_normalized_code(unit_code)}-{index:02d}"
            prerequisites = [previous] if previous else external
            nodes.append({
                "code": code,
                "parent_code": _normalized_code(unit_code),
                "knowledge_area": "NC",
                "knowledge_unit": unit_code,
                "title": title,
                "domain": "网络与通信",
                "description": f"CS2023 {unit_code}（{unit_titles[unit_code]}）主题：{title}。",
                "depth_level": unit_depth[unit_code],
                "evidence": f"能够解释“{title}”，画出关键数据流或状态变化，并使用协议字段、计算或实验结果验证",
                "prerequisites": prerequisites,
                "source_refs": [*ONTOLOGY_SOURCES, *NETWORK_PROTOCOL_SOURCES],
            })
            previous = code
        terminal[unit_code] = previous
    return nodes


def curriculum_nodes(focus_keys: list[str], track: str) -> list[dict[str, Any]]:
    """Return a discipline-isolated ontology for the current learning goal."""

    focus = focus_keys[0] if focus_keys else ""
    areas = FOCUS_AREAS.get(focus) or TRACK_AREAS.get(track) or TRACK_AREAS["计算机基础"]
    if areas == ("NC",):
        return _network_nodes()
    return _unit_nodes(areas, FOCUS_UNITS.get(focus))


def ontology_summary(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    areas = sorted({item["knowledge_area"] for item in nodes})
    units = sorted({item["knowledge_unit"] for item in nodes})
    return {
        "version": ONTOLOGY_VERSION,
        "authority": "ACM/IEEE-CS/AAAI CS2023",
        "knowledge_areas": areas,
        "knowledge_area_count": len(areas),
        "knowledge_units": units,
        "knowledge_unit_count": len(units),
        "node_count": len(nodes),
    }
