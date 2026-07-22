from __future__ import annotations

import json
import re
from typing import Any


class TeachingService:
    """Build a safe, structured classroom plan for synchronized narration and board work."""

    @staticmethod
    def _plain(source: str) -> str:
        value = re.sub(r"```[\s\S]*?```", " 代码示例 ", source)
        value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
        value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
        value = re.sub(r"[#>*_`|~-]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _formulas(source: str) -> list[str]:
        values = re.findall(r"\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$", source)
        return [next(item for item in pair if item).strip() for pair in values][:3]

    def fallback_plan(self, content: str) -> list[dict[str, Any]]:
        sections = [item for item in re.split(r"\n{2,}", content) if item.strip()]
        plan: list[dict[str, Any]] = []
        for index, section in enumerate(sections):
            plain = self._plain(section)
            if not plain:
                continue
            phrases = re.findall(r"\*\*([^*]{2,36})\*\*", section)
            heading = re.search(r"^#{1,6}\s+(.+)$", section.strip(), re.M)
            formulas = self._formulas(section)
            board_steps: list[str] = []
            for formula in formulas:
                board_steps.extend(
                    [
                        f"原式：{formula}",
                        "① 明确每个符号的物理或几何含义",
                        "② 按定义代入局部网格量并保持量纲一致",
                        "③ 检查符号、边界条件与退化情形",
                        "④ 得到判据，并用基准算例验证",
                    ]
                )
            if not board_steps and (heading or index == 0):
                board_steps = [f"本段主题：{(heading.group(1) if heading else plain[:28]).strip()}"]
            topic = heading.group(1).strip() if heading else (phrases[0] if phrases else "这一部分")
            narration = (
                f"我们先来看“{topic}”。这里不要急着记原文，先抓住它要解决的核心问题。"
                f"换个更直观的说法，{plain[:520]}。"
                "大家可以停一下想想：这个结论在什么条件下成立，又最容易在哪一步用错？"
            )
            plan.append(
                {
                    "section_index": index,
                    "narration": narration,
                    "focus_phrases": list(dict.fromkeys(phrases))[:3],
                    "formulas": formulas,
                    "board_steps": board_steps[:8],
                }
            )
        return plan

    @staticmethod
    def _extract_json(content: str) -> Any:
        cleaned = content.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            return json.loads(match.group(0)) if match else None

    def _normalize(self, value: Any, section_count: int) -> list[dict[str, Any]]:
        items = value.get("sections", []) if isinstance(value, dict) else []
        normalized = []
        for raw in items[:section_count]:
            if not isinstance(raw, dict):
                continue
            index = raw.get("section_index")
            if not isinstance(index, int) or not 0 <= index < section_count:
                continue
            narration = str(raw.get("narration") or "").strip()
            if not narration:
                continue
            normalized.append(
                {
                    "section_index": index,
                    "narration": narration[:1800],
                    "focus_phrases": [str(x)[:60] for x in raw.get("focus_phrases", [])[:4]],
                    "formulas": [str(x)[:300] for x in raw.get("formulas", [])[:3]],
                    "board_steps": [str(x)[:300] for x in raw.get("board_steps", [])[:10]],
                }
            )
        return sorted(normalized, key=lambda item: item["section_index"])

    async def create_plan(
        self,
        content: str,
        agent: Any,
        provider: Any,
        section_indices: list[int] | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        all_fallback = self.fallback_plan(content)
        requested = set(section_indices or [])
        fallback = [
            item for item in all_fallback
            if not requested or item["section_index"] in requested
        ]
        if not fallback:
            return {
                "sections": [],
                "generated_by": agent.name,
                "mode": "fallback",
                "fallback_reason": "所选章节没有可讲解内容",
            }
        raw_sections = [item for item in re.split(r"\n{2,}", content) if item.strip()]
        selected_content = "\n\n".join(
            f"[段落 {index}]\n{section}"
            for index, section in enumerate(raw_sections)
            if not requested or index in requested
        )[:10000]
        prompt = (
            "你是一位有感染力、会启发学生思考的真人课堂教师。请根据 Markdown 文档生成"
            "同步教学脚本。narration 不能照抄原文，必须用自然口语重新组织：先用问题或直观"
            "例子引入，再解释概念、强调易错点，最后小结并自然过渡；可以使用‘大家注意’、"
            "‘我们换个角度想’等自然课堂衔接，但不要堆砌口头禅。"
            "必须只输出 JSON 对象：{\"sections\":[{\"section_index\":0,"
            "\"narration\":\"口语讲稿\",\"focus_phrases\":[\"需要圈画的原文短语\"],"
            "\"formulas\":[\"LaTeX公式\"],\"board_steps\":[\"逐步公式推导或板书\"]}]}。"
            "focus_phrases 只能放 1 至 3 个确实出现在对应原文中的关键词或短语，禁止返回整句；"
            "section_index 必须与原文段落编号一致；讲到公式时给出有数学意义的逐步推导，"
            "不能虚构无法推出的等式；无公式的段落只板书一个核心结论。"
            f"只生成这些段落编号：{sorted(requested) if requested else '全部'}。\n\n"
            f"需要讲解的文档片段：\n{selected_content}"
        )
        try:
            response = await provider.chat(
                [
                    {"role": "system", "content": agent.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                model=model_name or agent.model,
                temperature=min(float(agent.temperature), 0.3),
            )
            normalized = self._normalize(
                self._extract_json(response.content), len(all_fallback)
            )
            if requested:
                normalized = [item for item in normalized if item["section_index"] in requested]
            if normalized:
                fallback_by_index = {item["section_index"]: item for item in fallback}
                for item in normalized:
                    fallback_by_index[item["section_index"]] = item
                return {
                    "sections": sorted(fallback_by_index.values(), key=lambda x: x["section_index"]),
                    "generated_by": agent.name,
                    "mode": "model",
                }
            reason = "模型没有返回可解析的教学脚本 JSON"
        except Exception as exc:
            reason = f"模型接口调用失败：{str(exc)[:180]}"
        return {
            "sections": fallback,
            "generated_by": agent.name,
            "mode": "fallback",
            "fallback_reason": reason,
        }


teaching_service = TeachingService()
