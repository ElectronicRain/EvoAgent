from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass
class TaskIntent:
    category: str
    goal: str
    actions: list[str]
    targets: list[str]
    required_capabilities: list[str]
    confidence: float
    needs_clarification: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class IntentService:
    """Deterministic first-pass intent routing used before the model is called."""

    _command = re.compile(
        r"(?:执行|运行|启动|测试|构建|安装|run|exec|execute|test|build|install)"
        r"(?:一下|命令|command)?|(?:^|\s)(?:npm|pnpm|yarn|pytest|python|pip|git|cargo|go)\s+",
        re.I,
    )
    _local = re.compile(
        r"桌面|本地|文件夹|目录|磁盘|硬盘|工作区|项目路径|文档|下载|"
        r"[A-Za-z]:[\\/]|(?:^|\s)[.~]{1,2}[\\/]",
        re.I,
    )
    _knowledge = re.compile(r"知识库|资料库|内部资料|文档依据|RAG|向量检索", re.I)
    _research = re.compile(
        r"联网|网页|网站|最新|近期|新闻|检索论文|文献检索|前沿文献|搜索资料|调研|综述|文献研究|web|online|search",
        re.I,
    )
    _change = re.compile(
        r"修改|修复|新增|添加|删除|重构|实现|完成|写入|保存|创建|"
        r"modify|fix|add|delete|refactor|implement|create|write",
        re.I,
    )
    _analysis = re.compile(r"分析|比较|解释|总结|审查|检查|评估|规划|设计|review|analy[sz]e|explain", re.I)

    def classify(self, text: str) -> TaskIntent:
        normalized = " ".join(text.strip().split())
        command = bool(self._command.search(normalized))
        local = bool(self._local.search(normalized))
        knowledge = bool(self._knowledge.search(normalized))
        research = bool(self._research.search(normalized))
        change = bool(self._change.search(normalized))

        # A workflow instruction such as “执行前沿文献检索” describes the research
        # goal, not a shell command. Route it to deterministic web research unless
        # the user also refers to a local resource/command environment.
        if research and not local:
            category = "web_research"
            capabilities = ["web_research", "mcp"]
        elif command:
            category = "command_execution"
            capabilities = ["exec", "local_files"]
        elif local and change:
            category = "local_workspace_change"
            capabilities = ["local_files", "exec"]
        elif local:
            category = "local_file_access"
            capabilities = ["local_files"]
        elif knowledge:
            category = "knowledge_retrieval"
            capabilities = ["knowledge", "mcp"]
        elif change:
            category = "implementation"
            capabilities = ["skills", "exec"]
        elif self._analysis.search(normalized):
            category = "analysis"
            capabilities = ["skills"]
        else:
            category = "conversation"
            capabilities = ["skills"]

        action_patterns = {
            "读取": r"读取|打开|查看|read|open",
            "搜索": r"搜索|查找|检索|search|find",
            "修改": r"修改|修复|编辑|重构|modify|fix|edit|refactor",
            "创建": r"新增|添加|创建|实现|add|create|implement",
            "执行": r"执行|运行|测试|构建|安装|run|exec|test|build|install",
            "分析": r"分析|比较|解释|总结|审查|评估|analy[sz]e|review|explain",
        }
        actions = [label for label, pattern in action_patterns.items() if re.search(pattern, normalized, re.I)]
        targets = []
        for match in re.findall(r"[`“\"]([^`”\"]{2,240})[`”\"]", normalized):
            if match not in targets:
                targets.append(match)
        for match in re.findall(r"[A-Za-z]:[\\/][^\s，。；;！？!?]+", normalized):
            cleaned = match.rstrip(",.)]}")
            if cleaned not in targets:
                targets.append(cleaned)

        goal = normalized[:300]
        vague = len(normalized) < 4 or bool(re.fullmatch(r"(?:处理|搞一下|看看|继续|这个|那个)[吧。！!]?", normalized))
        return TaskIntent(
            category=category,
            goal=goal,
            actions=actions or ["回答"],
            targets=targets,
            required_capabilities=capabilities,
            confidence=0.55 if vague else 0.92 if category != "conversation" else 0.72,
            needs_clarification=vague,
        )

    @staticmethod
    def prompt(intent: TaskIntent) -> str:
        targets = "、".join(intent.targets) or "由用户原始消息确定"
        return (
            "【结构化任务意图】\n"
            f"类型：{intent.category}\n"
            f"目标：{intent.goal}\n"
            f"动作：{'、'.join(intent.actions)}\n"
            f"目标对象：{targets}\n"
            f"所需能力：{'、'.join(intent.required_capabilities)}\n"
            "先核对目标和约束，再选择工具；工具执行后依据真实结果回答。"
        )


intent_service = IntentService()
