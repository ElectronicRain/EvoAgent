from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import settings


def _openai_compatible_urls(base_url: str, resource: str) -> list[str]:
    """Return preferred endpoint URLs without wasting a request on known /v1 APIs."""

    base = base_url.rstrip("/")
    suffix = resource.lstrip("/")
    if base.endswith("/v1"):
        return [f"{base}/{suffix}"]
    if "siliconflow.cn" in base.lower():
        return [f"{base}/v1/{suffix}"]
    return [f"{base}/{suffix}", f"{base}/v1/{suffix}"]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0


@dataclass
class ImageGenerationResponse:
    image_url: str
    revised_prompt: str = ""


class LLMProvider:
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
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
        top_p: float | None = None,
        max_output_tokens: int | None = None,
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
        elif (
            "jsxgraph-math-visualization" in system
            and re.search(
                r"(函数|方程|导数|积分|极限|解析几何|几何证明|向量|三角函数|概率|[xyz]\s*=)",
                user_message,
                re.I,
            )
        ):
            answer = (
                "## 数学推导\n\n"
                "以问题中的函数关系为例，先写成标准形式：\n\n"
                "$$f(x)=x^2$$\n\n"
                "对幂函数使用求导法则 $\\frac{d}{dx}x^n=nx^{n-1}$：\n\n"
                "$$f'(x)=2x$$\n\n"
                "因此在 $x=1$ 处的函数值与斜率分别为：\n\n"
                "$$f(1)=1,\\qquad f'(1)=2$$\n\n"
                "切线方程为：\n\n"
                "$$y-1=2(x-1)\\Longrightarrow y=2x-1$$\n\n"
                "```jsxgraph\n"
                '{"title":"函数与切线","boundingBox":[-3,7,3,-3],"axis":true,'
                '"objects":[{"type":"functiongraph","expression":"x^2","range":[-2.5,2.5],'
                '"name":"f(x)=x^2","color":"#1769c2"},{"type":"point","coords":[1,1],'
                '"name":"P(1,1)","color":"#d95f45"},{"type":"line","points":[[0,-1],[2,3]],'
                '"name":"y=2x-1","color":"#168c83"}]}\n'
                "```"
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
        self.request_options = dict(request_options or {})
        self.stream_response = bool(
            self.request_options.pop(
                "_stream_response",
                "siliconflow.cn" in self.base_url.lower(),
            )
        )
        self.retry_attempts = max(
            1,
            min(
                int(
                    self.request_options.pop(
                        "_retry_attempts",
                        self.request_options.pop("retry_attempts", 3),
                    )
                ),
                5,
            ),
        )
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _response_from_data(data: dict[str, Any]) -> LLMResponse:
        try:
            choice = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("在线模型接口响应格式无效，缺少 choices[0].message") from exc
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

    @staticmethod
    async def _stream_response_data(response: httpx.Response) -> LLMResponse:
        content_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}
        total_tokens = 0
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            total_tokens = max(
                total_tokens,
                int((data.get("usage") or {}).get("total_tokens") or 0),
            )
            choices = data.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or choices[0].get("message") or {}
            if delta.get("content"):
                content_parts.append(str(delta["content"]))
            for item in delta.get("tool_calls") or []:
                index = int(item.get("index") or 0)
                current = tool_parts.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if item.get("id"):
                    current["id"] = str(item["id"])
                function = item.get("function") or {}
                if function.get("name"):
                    current["name"] += str(function["name"])
                if function.get("arguments"):
                    current["arguments"] += str(function["arguments"])
        tool_calls = []
        for item in tool_parts.values():
            try:
                arguments = json.loads(item["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {"id": item["id"] or None, "name": item["name"], "arguments": arguments}
            )
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            tokens=total_tokens,
        )

    @staticmethod
    def _transport_error_detail(exc: Exception | None) -> str:
        if exc is None:
            return "未知连接错误"
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail or '模型服务未返回错误正文'}"

    async def health_check(self, model: str) -> dict[str, Any]:
        """Check credentials and model availability without creating billable output."""

        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 30)) as client:
            for url in _openai_compatible_urls(self.base_url, "models"):
                response = await client.get(url, headers=headers)
                if response.status_code not in {400, 404}:
                    break
        if response is None:
            raise RuntimeError("模型接口健康检查没有收到响应")
        if response.is_error:
            detail = response.text.replace("\n", " ")[:500]
            raise RuntimeError(
                f"模型接口健康检查返回 HTTP {response.status_code}: {detail or '无错误正文'}"
            )
        try:
            items = response.json().get("data") or []
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("模型接口健康检查响应不是有效 JSON") from exc
        model_ids = {
            str(item.get("id") or "") for item in items if isinstance(item, dict)
        }
        return {
            "model_available": not model_ids or model in model_ids,
            "model_count": len(model_ids),
        }

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
        top_p: float | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        payload.update(self.request_options)
        if top_p is not None:
            payload["top_p"] = top_p
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.stream_response:
            payload["stream"] = True
        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        response: httpx.Response | None = None
        last_transport_error: Exception | None = None
        attempts_made = 0
        stopped_to_avoid_duplicate_billing = False
        estimated_prompt_tokens = max(
            1,
            len(json.dumps(messages, ensure_ascii=False, default=str)) // 4,
        )
        retryable_statuses = {408, 409, 425, 429, 500, 502, 503, 504}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(1, self.retry_attempts + 1):
                attempts_made = attempt
                try:
                    for url in _openai_compatible_urls(self.base_url, "chat/completions"):
                        if self.stream_response:
                            async with client.stream(
                                "POST", url, json=payload, headers=headers
                            ) as streamed:
                                if streamed.is_error:
                                    await streamed.aread()
                                    response = streamed
                                else:
                                    content_type = streamed.headers.get("content-type", "").lower()
                                    if "text/event-stream" in content_type:
                                        result = await self._stream_response_data(streamed)
                                        if result.tokens <= 0:
                                            result.tokens = estimated_prompt_tokens + max(
                                                1, len(result.content) // 4
                                            )
                                        return result
                                    await streamed.aread()
                                    return self._response_from_data(streamed.json())
                        else:
                            response = await client.post(url, json=payload, headers=headers)
                        if response.status_code not in {400, 404}:
                            break
                    if (
                        response is not None
                        and
                        response.status_code in retryable_statuses
                        and attempt < self.retry_attempts
                    ):
                        await asyncio.sleep(min(0.6 * attempt, 1.8))
                        continue
                    break
                except httpx.TransportError as exc:
                    last_transport_error = exc
                    retry_is_safe = isinstance(
                        exc,
                        (httpx.ConnectError, httpx.ConnectTimeout),
                    )
                    if not retry_is_safe:
                        stopped_to_avoid_duplicate_billing = True
                        break
                    if attempt >= self.retry_attempts:
                        break
                    await asyncio.sleep(min(0.6 * attempt, 1.8))
        if response is None:
            detail = self._transport_error_detail(last_transport_error)
            suffix = (
                "；请求可能已被模型服务接收，为避免重复计费未自动重试"
                if stopped_to_avoid_duplicate_billing
                else ""
            )
            raise RuntimeError(
                f"在线模型接口连接失败（已发起 {attempts_made} 次请求）：{detail}{suffix}"
            )
        if response.is_error:
            detail = response.text.replace("\n", " ")[:500]
            raise RuntimeError(
                f"模型接口返回 HTTP {response.status_code}（已尝试 {attempts_made} 次）: "
                f"{detail or '无错误正文'}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("在线模型接口响应不是有效 JSON") from exc
        return self._response_from_data(data)


class OpenAICompatibleImageProvider:
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

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
    ) -> ImageGenerationResponse:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
        }
        payload.update(self.request_options)
        headers = dict(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = None
            for url in _openai_compatible_urls(self.base_url, "images/generations"):
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code not in {400, 404}:
                    break
            if response is None:
                raise RuntimeError("图片模型接口没有收到响应")
            if response.is_error:
                detail = response.text.replace("\n", " ")[:500]
                raise RuntimeError(
                    f"图片模型接口返回 HTTP {response.status_code}: {detail or '无错误正文'}"
                )
            data = response.json()
        items = data.get("data") or data.get("images") or []
        if not items:
            raise RuntimeError("图片模型接口没有返回图片")
        item = items[0] if isinstance(items[0], dict) else {"url": items[0]}
        image_url = str(item.get("url") or item.get("image_url") or "")
        if not image_url and item.get("b64_json"):
            image_url = f"data:image/png;base64,{item['b64_json']}"
        if not image_url:
            raise RuntimeError("图片模型响应缺少 url 或 b64_json")
        return ImageGenerationResponse(
            image_url=image_url,
            revised_prompt=str(item.get("revised_prompt") or ""),
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


def image_provider_from_endpoint(endpoint: Any) -> OpenAICompatibleImageProvider:
    from .common import loads
    from .secrets import secret_store

    return OpenAICompatibleImageProvider(
        endpoint.base_url,
        secret_store.decrypt(endpoint.api_key_ciphertext),
        headers=loads(endpoint.headers_json, {}),
        request_options=loads(endpoint.request_options_json, {}),
        timeout_seconds=endpoint.timeout_seconds,
    )
