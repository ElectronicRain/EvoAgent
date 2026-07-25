from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0


class LLMProvider:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        raise NotImplementedError


class DemoProvider(LLMProvider):
    """Offline provider so every screen and workflow remains testable without an API key."""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        user_message = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
            "",
        )
        system = str(messages[0].get("content", "")) if messages else ""
        knowledge = ""
        if "【知识库检索结果】" in system:
            knowledge = system.split("【知识库检索结果】", 1)[1].strip()
        web_sources = ""
        if "【网络研究资料】" in system:
            web_sources = system.split("【网络研究资料】", 1)[1].strip()
        local_result: dict[str, Any] | None = None
        local_match = re.search(
            r"【本地请求预检结果】.*?结果：(\{.*?\})\n应优先依据该本地结果回答",
            system,
            re.S,
        )
        if local_match:
            try:
                local_result = json.loads(local_match.group(1))
            except json.JSONDecodeError:
                local_result = None
        role_hint = system.splitlines()[0][:80] if system else "EvoAgent"
        if local_result:
            payload = local_result.get("result") or {}
            if local_result.get("status") == "completed" and isinstance(payload, dict):
                if isinstance(payload.get("items"), list):
                    rows = [
                        f"- {item.get('name')}（{item.get('type')}）"
                        for item in payload["items"][:100]
                    ]
                    answer = (
                        f"已读取本地目录：{payload.get('path', '当前目录')}\n\n"
                        + ("\n".join(rows) or "该目录为空。")
                    )
                elif "content" in payload:
                    answer = (
                        f"已读取本地文件：{payload.get('path', '')}\n\n"
                        f"{str(payload.get('content', ''))[:8000]}"
                    )
                elif isinstance(payload.get("matches"), list):
                    answer = (
                        f"已完成本地搜索，共找到 {len(payload['matches'])} 项：\n\n"
                        + "\n".join(
                            f"- {item.get('path')}"
                            for item in payload["matches"][:100]
                        )
                    )
                else:
                    answer = f"本地操作已完成：{json.dumps(payload, ensure_ascii=False)}"
            else:
                answer = (
                    "本地操作未执行。\n\n"
                    f"原因：{local_result.get('error') or local_result.get('message') or '当前安全策略不允许该操作'}"
                )
        elif web_sources:
            review_match = re.search(r"请对任务“(.+?)”", user_message)
            research_topic = review_match.group(1) if review_match else user_message
            entries = re.findall(
                r"\[(\d+)\] (.*?)\nURL: (.*?)\n来源: (.*?)\n内容: (.*?)(?=\n\n\[\d+\]|\Z)",
                web_sources,
                flags=re.S,
            )
            evidence = []
            for number, title, url, source, content in entries[:6]:
                excerpt = " ".join(content.split())[:420]
                evidence.append(
                    f"### {number}. {title}\n\n{excerpt or '仅取得题录信息，正文需进一步核验。'}\n\n来源：[{source}]({url})"
                )
            answer = (
                "## 综述摘要\n\n"
                f"围绕“{research_topic}”，EvoAgent 已完成公开网页与学术元数据检索，"
                f"本轮纳入 {len(entries)} 条可追溯来源。以下内容按概念、评价维度、方法与局限组织。\n\n"
                "## 主题脉络与证据摘录\n\n"
                + ("\n\n".join(evidence) or "未取得足够的可解析正文，建议更换检索词后重试。")
                + "\n\n## 综合结论\n\n"
                "现有资料表明，质量评估应同时覆盖几何有效性、单元形状、尺寸变化、"
                "方向一致性以及对后续数值离散误差的影响。不同指标的适用边界并不相同，"
                "因此应采用多指标组合，并用目标求解器或基准算例验证阈值。\n\n"
                "## 局限与待核验项\n\n"
                "网页可访问性与题录完整度会影响证据覆盖；仅获得摘要或元数据的来源不能代替全文审读。"
            )
        else:
            answer = (
                f"已由离线演示模型处理任务：{user_message}\n\n"
                f"当前角色：{role_hint}\n"
                "建议执行路径：\n"
                "1. 明确任务目标与可验证的输出标准。\n"
                "2. 将复杂任务拆成检索、分析、核验和交付四个环节。\n"
                "3. 对关键结论保留来源、运行轨迹和人工复核入口。"
            )
        if knowledge:
            excerpt = knowledge[:800]
            answer += f"\n\n依据已接入知识库：\n{excerpt}\n\n引用：见上述知识片段。"
        answer += "\n\n提示：当前为离线演示模型，配置 OpenAI 兼容模型后可获得真实推理结果。"
        return LLMResponse(content=answer, tokens=max(1, len(answer) // 4))


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        headers: dict[str, str] | None = None,
        request_options: dict[str, Any] | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = headers or {}
        self.request_options = request_options or {}
        self.timeout_seconds = timeout_seconds

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        payload.update(self.request_options)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
            if response.status_code in {400, 404} and not self.base_url.endswith("/v1"):
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions", json=payload, headers=headers
                )
            if response.is_error:
                detail = response.text.replace("\n", " ")[:500]
                raise RuntimeError(
                    f"模型接口返回 HTTP {response.status_code}: {detail or '无错误正文'}"
                )
            data = response.json()
        choice = data["choices"][0]["message"]
        tool_calls = []
        for item in choice.get("tool_calls") or []:
            function = item.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {"id": item.get("id"), "name": function.get("name"), "arguments": arguments}
            )
        return LLMResponse(
            content=choice.get("content") or "",
            tool_calls=tool_calls,
            tokens=int((data.get("usage") or {}).get("total_tokens") or 0),
        )


def get_provider(name: str) -> LLMProvider:
    if name == "demo" or not settings.llm_api_key:
        return DemoProvider()
    if name in {"openai", "openai-compatible", "spark"}:
        return OpenAICompatibleProvider(settings.llm_base_url, settings.llm_api_key)
    raise ValueError(f"不支持的模型供应商: {name}")


def provider_from_endpoint(endpoint: Any) -> LLMProvider:
    from .common import loads
    from .secrets import secret_store

    return OpenAICompatibleProvider(
        endpoint.base_url,
        secret_store.decrypt(endpoint.api_key_ciphertext),
        headers=loads(endpoint.headers_json, {}),
        request_options=loads(endpoint.request_options_json, {}),
        timeout_seconds=endpoint.timeout_seconds,
    )
