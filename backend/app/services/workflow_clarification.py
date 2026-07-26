from __future__ import annotations

import re
from typing import Any


Question = dict[str, Any]


class WorkflowClarificationService:
    """Build a small, deterministic requirement gate before a workflow is run.

    The gate intentionally does not call an LLM: starting a workflow must remain
    reliable when a model endpoint is unavailable, and the user's answers must not
    be silently reinterpreted.  Rules are task-family based and only ask for
    decisions that materially change the result.
    """

    _task_patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "literature_review",
            re.compile(
                r"综述|文献回顾|文献评述|系统评价|系统综述|meta\s*分析|元分析|"
                r"literature\s+review|systematic\s+review|review\s+article|meta[-\s]?analysis",
                re.I,
            ),
        ),
        ("translation", re.compile(r"翻译|译成|中译英|英译中|translate|translation", re.I)),
        (
            "presentation",
            re.compile(r"PPT|幻灯片|演示文稿|汇报材料|路演|slide\s*deck|presentation", re.I),
        ),
        (
            "data_analysis",
            re.compile(
                r"数据分析|分析.{0,12}数据|(?:数据|CSV|Excel|表格|样本).{0,20}分析|"
                r"统计分析|建模分析|可视化分析|回归分析|聚类|预测模型|"
                r"analy[sz]e\s+(?:the\s+)?data|data\s+analysis",
                re.I,
            ),
        ),
        (
            "implementation",
            re.compile(
                r"开发|实现|编程|写代码|修复|重构|搭建|部署|接口开发|网页|应用|系统|"
                r"implement|develop|code|refactor|deploy|debug|fix",
                re.I,
            ),
        ),
        (
            "planning",
            re.compile(r"方案|规划|计划|策划|路线图|实施路径|proposal|roadmap|plan", re.I),
        ),
        (
            "writing",
            re.compile(
                r"撰写|写一篇|生成一篇|报告|文章|论文|文案|脚本|说明书|手册|"
                r"write|draft|report|article|essay",
                re.I,
            ),
        ),
        (
            "research",
            re.compile(
                r"调研|研究|检索|搜索资料|竞品分析|行业分析|research|investigate|search", re.I
            ),
        ),
    )

    _language = re.compile(
        r"中文(?:版|撰写|输出)?|英文(?:版|撰写|输出)?|双语|中英(?:文)?对照|"
        r"in\s+(?:Chinese|English)|Chinese\s+version|English\s+version",
        re.I,
    )
    _length = re.compile(
        r"\d+\s*(?:字|词|页|分钟|页PPT|张幻灯片|words?|pages?|slides?)|"
        r"(?:篇幅|字数|长度).{0,8}\d+",
        re.I,
    )
    _audience = re.compile(
        r"面向|读者|受众|给.{0,12}(?:看|使用|汇报)|本科生|研究生|教师|专家|管理层|"
        r"客户|投资人|audience|for\s+(?:students?|teachers?|experts?|customers?|executives?)",
        re.I,
    )
    _literature_count = re.compile(
        r"(?:至少|不少于|约|大约|选取|检索|包含|覆盖)?\s*\d+\s*(?:篇|条)\s*(?:文献|论文|资料)?|"
        r"\d+\s*(?:papers?|articles?|references?|studies)",
        re.I,
    )
    _time_range = re.compile(
        r"近\s*\d+\s*年|最近\s*\d+\s*年|过去\s*\d+\s*年|\d{4}\s*[-—至到]\s*\d{4}|"
        r"不限年份|全时期|近年|latest|recent\s+\d+\s+years?|since\s+\d{4}",
        re.I,
    )
    _review_method = re.compile(
        r"叙述性综述|系统综述|范围综述|系统评价|meta\s*分析|元分析|"
        r"narrative\s+review|systematic\s+review|scoping\s+review|meta[-\s]?analysis",
        re.I,
    )
    _focus = re.compile(
        r"重点|聚焦|围绕.{2,24}(?:问题|方向|机制|方法|应用|趋势|方面)|核心问题|研究问题|"
        r"focus(?:ed)?\s+on|research\s+question",
        re.I,
    )
    _data_source = re.compile(
        r"数据(?:集|源|文件|库)|CSV|Excel|表格|问卷|样本|日志|数据库|附件|上传|"
        r"dataset|data\s+source|\.csv|\.xlsx?|SQL",
        re.I,
    )
    _analysis_goal = re.compile(
        r"分析.{0,16}(?:原因|关系|趋势|差异|影响|相关|分布)|预测|分类|聚类|回归|检验|"
        r"指标|目标变量|因变量|hypothesis|predict|classif|correlation|regression",
        re.I,
    )
    _analysis_deliverable = re.compile(
        r"分析报告|报告.{0,8}图表|图表.{0,8}报告|可视化看板|仪表盘|清洗后数据|模型结果|"
        r"dashboard|report\s+and\s+charts?|cleaned\s+data|model\s+results?",
        re.I,
    )
    _tech_stack = re.compile(
        r"Python|Java|Go|Rust|C\+\+|C#|TypeScript|JavaScript|Vue|React|Angular|"
        r"FastAPI|Django|Spring|Node(?:\.js)?|Windows|Linux|Android|iOS|Web|桌面端|移动端",
        re.I,
    )
    _acceptance = re.compile(
        r"验收|测试通过|成功标准|完成标准|必须支持|确保|性能|并发|响应时间|兼容|"
        r"acceptance|success\s+criteria|must\s+support|latency|throughput",
        re.I,
    )
    _change_scope = re.compile(
        r"最小(?:必要)?改动|仅修改|不要重构|允许重构|模块级|架构级|整体重构|"
        r"minimal\s+change|module[-\s]level|architectural\s+change",
        re.I,
    )
    _deadline = re.compile(
        r"截止|工期|周期|预算|成本|在\s*\d+\s*(?:天|周|月)内|\d+\s*(?:天|周|个月)|"
        r"deadline|budget|within\s+\d+",
        re.I,
    )
    _target_language = re.compile(
        r"译成|翻译成|目标语言|中译英|英译中|to\s+(?:Chinese|English|Japanese|Korean|French|German)",
        re.I,
    )
    _tone = re.compile(
        r"正式|学术|口语|简洁|专业|通俗|严谨|活泼|营销|视觉|语气|风格|"
        r"tone|formal|academic|casual|visual",
        re.I,
    )
    _research_depth = re.compile(
        r"快速扫描|快速调研|标准调研|深度调研|深入研究|多来源交叉验证|系统搜集|"
        r"brief\s+scan|standard\s+research|deep\s+research|in-depth",
        re.I,
    )

    @staticmethod
    def _choice(
        question_id: str,
        label: str,
        question: str,
        options: list[tuple[str, str, str]],
        default: str,
    ) -> Question:
        return {
            "id": question_id,
            "label": label,
            "question": question,
            "type": "single_choice",
            "required": True,
            "default": default,
            "options": [
                {"value": value, "label": option_label, "description": description}
                for value, option_label, description in options
            ],
        }

    @staticmethod
    def _number(
        question_id: str,
        label: str,
        question: str,
        default: int,
        minimum: int,
        maximum: int,
        suffix: str,
    ) -> Question:
        return {
            "id": question_id,
            "label": label,
            "question": question,
            "type": "number",
            "required": True,
            "default": default,
            "min": minimum,
            "max": maximum,
            "suffix": suffix,
        }

    @staticmethod
    def _text(
        question_id: str,
        label: str,
        question: str,
        default: str,
        placeholder: str,
        *,
        required: bool = True,
    ) -> Question:
        return {
            "id": question_id,
            "label": label,
            "question": question,
            "type": "text",
            "required": required,
            "default": default,
            "placeholder": placeholder,
        }

    def _task_type(self, task: str, context: str) -> str:
        combined = f"{task}\n{context}"
        for task_type, pattern in self._task_patterns:
            if pattern.search(combined):
                return task_type
        return "generic"

    def analyze(
        self,
        task: str,
        *,
        workflow_name: str = "",
        workflow_description: str = "",
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = " ".join(task.strip().split())
        context = " ".join(f"{workflow_name} {workflow_description}".split())
        combined = f"{normalized} {context}".strip()
        task_type = self._task_type(normalized, context)
        questions: list[Question] = []

        if task_type == "literature_review":
            if not self._language.search(combined):
                questions.append(
                    self._choice(
                        "output_language",
                        "输出语言",
                        "这篇综述需要使用什么语言？",
                        [
                            ("zh-CN", "中文", "适合中文阅读、教学或国内项目交付"),
                            ("en", "英文", "适合英文论文初稿或国际交流"),
                            ("bilingual", "中英双语", "同时提供中英文标题、摘要与关键结论"),
                        ],
                        "zh-CN",
                    )
                )
            if not self._literature_count.search(combined):
                questions.append(
                    self._number(
                        "literature_count",
                        "文献规模",
                        "计划检索并纳入多少篇核心文献？",
                        30,
                        5,
                        500,
                        "篇",
                    )
                )
            if not self._time_range.search(combined):
                questions.append(
                    self._choice(
                        "literature_time_range",
                        "文献时间范围",
                        "文献检索覆盖哪个时间范围？",
                        [
                            ("recent_5_years", "近 5 年", "突出较新的研究进展"),
                            ("recent_10_years", "近 10 年", "兼顾发展脉络与近期成果"),
                            ("all_years", "不限年份", "包含经典文献与最新成果"),
                        ],
                        "recent_5_years",
                    )
                )
            if not self._review_method.search(combined):
                questions.append(
                    self._choice(
                        "review_method",
                        "综述类型",
                        "希望采用哪一种综述方式？",
                        [
                            ("narrative", "叙述性综述", "梳理发展脉络、代表方法与趋势"),
                            ("systematic", "系统综述", "明确检索、筛选与纳入标准"),
                            ("scoping", "范围综述", "快速描绘研究版图与主题分布"),
                        ],
                        "narrative",
                    )
                )
            if not self._focus.search(normalized) and len(questions) < 5:
                questions.append(
                    self._text(
                        "review_focus",
                        "重点方向",
                        "最希望综述重点回答什么问题？",
                        "发展脉络、核心方法、代表性成果、主要争议与未来趋势",
                        "例如：重点比较不同技术路线的效果和适用场景",
                    )
                )

        elif task_type == "translation":
            if not self._target_language.search(normalized):
                questions.append(
                    self._choice(
                        "target_language",
                        "目标语言",
                        "内容需要翻译成哪种语言？",
                        [
                            ("zh-CN", "简体中文", "输出自然、准确的简体中文"),
                            ("en", "英文", "输出自然、准确的英文"),
                            ("bilingual", "中英对照", "按段落保留原文与译文"),
                        ],
                        "zh-CN",
                    )
                )
            if not self._tone.search(normalized):
                questions.append(
                    self._choice(
                        "translation_style",
                        "翻译风格",
                        "译文采用什么表达风格？",
                        [
                            ("faithful", "准确忠实", "优先保持术语、含义和结构"),
                            ("academic", "学术严谨", "适合论文和研究材料"),
                            ("natural", "自然易读", "在准确基础上优化目标语言表达"),
                        ],
                        "faithful",
                    )
                )

        elif task_type == "presentation":
            if not self._audience.search(normalized):
                questions.append(
                    self._text(
                        "target_audience",
                        "汇报对象",
                        "这份演示主要面向谁？",
                        "不了解项目细节的专业听众",
                        "例如：评审专家、管理层、本科生",
                    )
                )
            if not self._length.search(normalized):
                questions.append(
                    self._number("slide_count", "页数", "演示文稿计划包含多少页？", 12, 3, 80, "页")
                )
            if not self._language.search(normalized):
                questions.append(
                    self._choice(
                        "output_language",
                        "输出语言",
                        "演示文稿使用什么语言？",
                        [
                            ("zh-CN", "中文", "中文标题与正文"),
                            ("en", "英文", "英文标题与正文"),
                            ("bilingual", "中英双语", "重要内容中英对照"),
                        ],
                        "zh-CN",
                    )
                )
            if not self._tone.search(normalized):
                questions.append(
                    self._choice(
                        "presentation_style",
                        "呈现风格",
                        "希望采用哪种呈现风格？",
                        [
                            ("professional", "专业简洁", "结论先行、适合正式汇报"),
                            ("academic", "学术严谨", "强调方法、证据与引用"),
                            ("visual", "视觉叙事", "减少长文本、突出图表和故事线"),
                        ],
                        "professional",
                    )
                )

        elif task_type == "data_analysis":
            if not self._data_source.search(normalized):
                questions.append(
                    self._text(
                        "data_source",
                        "数据来源",
                        "需要分析的数据来自哪里？",
                        "使用本次任务提供或工作流已连接的数据；若无数据则先生成数据需求清单",
                        "例如：已上传的 Excel、业务数据库、问卷数据",
                    )
                )
            if not self._analysis_goal.search(normalized):
                questions.append(
                    self._text(
                        "analysis_goal",
                        "分析目标",
                        "分析需要回答的核心问题是什么？",
                        "识别关键趋势、异常、影响因素并给出可执行建议",
                        "例如：找出转化率下降的主要原因",
                    )
                )
            if not self._analysis_deliverable.search(normalized):
                questions.append(
                    self._choice(
                        "analysis_deliverable",
                        "交付形式",
                        "希望获得哪种分析成果？",
                        [
                            ("report_charts", "报告 + 图表", "提供结论、图表与方法说明"),
                            ("dashboard", "指标看板", "突出可持续跟踪的指标"),
                            ("data_model", "数据与模型结果", "提供清洗数据、模型指标和解释"),
                        ],
                        "report_charts",
                    )
                )

        elif task_type == "implementation":
            if not self._tech_stack.search(normalized):
                questions.append(
                    self._text(
                        "runtime_platform",
                        "运行环境",
                        "功能需要运行在哪个平台或技术环境？",
                        "沿用当前项目的技术栈与本地运行环境",
                        "例如：Windows 桌面端、Vue + FastAPI、Python 3.12",
                    )
                )
            if not self._acceptance.search(normalized):
                questions.append(
                    self._text(
                        "acceptance_criteria",
                        "验收标准",
                        "满足哪些条件才算任务完成？",
                        "功能可完整运行、异常有明确提示、已有功能不回归并通过自动化测试",
                        "描述必须支持的行为、性能或测试结果",
                    )
                )
            if not self._change_scope.search(normalized):
                questions.append(
                    self._choice(
                        "change_scope",
                        "改动范围",
                        "本次实现允许采用多大的改动范围？",
                        [
                            ("minimal", "最小必要改动", "优先复用现有结构和接口"),
                            ("module", "模块级优化", "允许重构相关模块以保证质量"),
                            ("architecture", "架构级调整", "允许调整跨模块设计与数据结构"),
                        ],
                        "module",
                    )
                )

        elif task_type == "planning":
            if not self._audience.search(normalized):
                questions.append(
                    self._text(
                        "target_users",
                        "目标对象",
                        "方案主要服务于谁？",
                        "当前业务场景中的核心用户",
                        "例如：高校教师、研发团队、企业客户",
                    )
                )
            if not self._deadline.search(normalized):
                questions.append(
                    self._text(
                        "time_budget",
                        "时间与资源",
                        "方案有哪些时间、预算或资源约束？",
                        "先给出可落地的分阶段方案，关键资源待确认",
                        "例如：3 个月内完成，预算 20 万",
                    )
                )
            if not self._acceptance.search(normalized):
                questions.append(
                    self._text(
                        "success_criteria",
                        "成功标准",
                        "如何判断方案实施成功？",
                        "目标可衡量、里程碑可验收、风险有应对措施",
                        "例如：试点用户留存率提升 20%",
                    )
                )

        elif task_type == "writing":
            if not self._language.search(normalized):
                questions.append(
                    self._choice(
                        "output_language",
                        "输出语言",
                        "文稿使用什么语言？",
                        [
                            ("zh-CN", "中文", "中文成稿"),
                            ("en", "英文", "英文成稿"),
                            ("bilingual", "中英双语", "重要部分中英对照"),
                        ],
                        "zh-CN",
                    )
                )
            if not self._audience.search(normalized):
                questions.append(
                    self._text(
                        "target_audience",
                        "目标读者",
                        "文稿主要写给谁看？",
                        "具备基础背景的一般读者",
                        "例如：评审专家、客户、大学生",
                    )
                )
            if not self._length.search(normalized):
                questions.append(
                    self._number(
                        "target_length", "篇幅", "期望成稿大约多少字？", 3000, 200, 100000, "字"
                    )
                )
            if not self._tone.search(normalized):
                questions.append(
                    self._choice(
                        "writing_style",
                        "写作风格",
                        "文稿采用什么表达风格？",
                        [
                            ("professional", "专业清晰", "结构清楚、结论明确"),
                            ("academic", "学术严谨", "强调证据、术语和引用"),
                            ("popular", "通俗易懂", "减少术语并增加解释和例子"),
                        ],
                        "professional",
                    )
                )

        elif task_type == "research":
            if not self._focus.search(normalized) and len(normalized) < 80:
                questions.append(
                    self._text(
                        "research_question",
                        "核心问题",
                        "本次调研最需要回答什么问题？",
                        "梳理现状、关键参与者、主要差异、风险与趋势",
                        "例如：比较前三类方案的成本、效果与适用边界",
                    )
                )
            if not self._time_range.search(normalized):
                questions.append(
                    self._choice(
                        "research_time_range",
                        "信息时效",
                        "调研资料重点覆盖哪个时期？",
                        [
                            ("recent_1_year", "近 1 年", "突出最新动态"),
                            ("recent_5_years", "近 5 年", "兼顾趋势与代表资料"),
                            ("all_years", "不限年份", "同时纳入经典与最新资料"),
                        ],
                        "recent_5_years",
                    )
                )
            if not self._research_depth.search(normalized):
                questions.append(
                    self._choice(
                        "research_depth",
                        "调研深度",
                        "希望调研达到什么深度？",
                        [
                            ("brief", "快速扫描", "给出核心结论和少量关键来源"),
                            ("standard", "标准调研", "多来源交叉验证并形成结构化报告"),
                            ("deep", "深度研究", "系统搜集、逐项比较并分析证据质量"),
                        ],
                        "standard",
                    )
                )

        else:
            vague = len(normalized) < 18 or bool(
                re.fullmatch(
                    r"(?:帮我|请)?(?:处理|完成|优化|分析|生成|做|写)(?:一下|这个|它)?[。！!]?",
                    normalized,
                )
            )
            if vague:
                questions.extend(
                    [
                        self._text(
                            "expected_deliverable",
                            "期望成果",
                            "最终需要交付什么？",
                            "一份结构化结果，包含结论、依据和下一步行动",
                            "例如：分析报告、可运行功能、数据表",
                        ),
                        self._text(
                            "task_scope",
                            "任务范围",
                            "本次任务需要覆盖哪些内容，哪些内容不需要做？",
                            "完成实现目标所必需的核心范围",
                            "补充对象、边界和优先级",
                        ),
                        self._text(
                            "success_criteria",
                            "完成标准",
                            "什么结果才算符合你的预期？",
                            "结果正确、完整、可验证，并清楚说明限制",
                            "例如：通过测试、包含 3 个方案对比",
                        ),
                    ]
                )

        questions = questions[:5]
        labels = {
            "literature_review": "文献综述",
            "translation": "翻译",
            "presentation": "演示文稿",
            "data_analysis": "数据分析",
            "implementation": "开发实现",
            "planning": "方案规划",
            "writing": "内容写作",
            "research": "研究调研",
            "generic": "通用任务",
        }
        return {
            "required": bool(questions),
            "task_type": task_type,
            "task_type_label": labels[task_type],
            "summary": (
                f"识别为{labels[task_type]}任务；还有 {len(questions)} 项关键要求需要确认。"
                if questions
                else f"识别为{labels[task_type]}任务；当前描述已足够明确，可以直接运行。"
            ),
            "questions": questions,
            "original_task": task.strip(),
            "resolved_task": task.strip() if not questions else "",
            "definition_node_count": len((definition or {}).get("nodes") or []),
        }

    @staticmethod
    def _answer_label(question: Question, answer: Any) -> str:
        if question.get("type") == "single_choice":
            option = next(
                (item for item in question.get("options", []) if item.get("value") == answer),
                None,
            )
            return str((option or {}).get("label") or answer).strip()
        text = str(answer).strip()
        suffix = str(question.get("suffix") or "")
        if suffix and text and not text.endswith(suffix):
            return f"{text}{suffix}"
        return text

    def resolve(
        self,
        task: str,
        answers: dict[str, Any],
        *,
        workflow_name: str = "",
        workflow_description: str = "",
        definition: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        analysis = self.analyze(
            task,
            workflow_name=workflow_name,
            workflow_description=workflow_description,
            definition=definition,
        )
        requirements: list[dict[str, str]] = []
        for question in analysis["questions"]:
            value = answers.get(question["id"], question.get("default"))
            if isinstance(value, str):
                value = value.strip()
            if question.get("required") and (value is None or value == ""):
                raise ValueError(f"请补充“{question['label']}”后再运行")
            if value is None or value == "":
                continue
            if question.get("type") == "number":
                try:
                    numeric = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"“{question['label']}”必须填写数字") from exc
                if numeric < int(question.get("min", numeric)) or numeric > int(
                    question.get("max", numeric)
                ):
                    raise ValueError(
                        f"“{question['label']}”应在 {question.get('min')} 到 {question.get('max')} 之间"
                    )
                value = numeric
            requirements.append(
                {
                    "id": question["id"],
                    "label": question["label"],
                    "value": self._answer_label(question, value),
                }
            )

        if requirements:
            requirement_text = "\n".join(
                f"- {item['label']}：{item['value']}" for item in requirements
            )
            resolved_task = (
                f"{task.strip()}\n\n"
                "【运行前已确认的执行要求】\n"
                f"{requirement_text}\n"
                "请严格依据以上已确认要求完成任务；不得擅自缩减范围或改变交付形式。"
            )
        else:
            resolved_task = task.strip()

        return {
            **analysis,
            "required": False,
            "confirmed": True,
            "requirements": requirements,
            "resolved_task": resolved_task,
            "summary": f"已确认 {len(requirements)} 项执行要求，可以启动工作流。",
        }


workflow_clarification_service = WorkflowClarificationService()
