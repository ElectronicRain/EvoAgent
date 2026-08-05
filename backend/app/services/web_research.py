from __future__ import annotations

import asyncio
import html
import ipaddress
import re
from datetime import date
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote_plus, urlparse
from uuid import uuid4
from xml.etree import ElementTree

import httpx

from .research_routing import research_routing_service


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class HumanVerificationRequired(RuntimeError):
    def __init__(self, provider: str, url: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.url = url
        self.status_code = status_code
        detail = f"HTTP {status_code}" if status_code else "检测到机器人验证页"
        super().__init__(f"{provider} 需要用户完成机器人验证（{detail}）")


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

    def text(self) -> str:
        return " ".join(self.parts)


class WebResearchService:
    verification_wait_seconds = 90
    safe_scholar_cookie_names = {
        "GSP",
        "GOOGLE_ABUSE_EXEMPTION",
        "_GRECAPTCHA",
    }
    def __init__(self) -> None:
        self._verification_sessions: dict[str, dict[str, Any]] = {}
        self._scholar_cookie_header = ""

    @staticmethod
    def mesh_domain(task: str) -> str | None:
        text = task.lower()
        if not re.search(r"网格质量|mesh\s+quality|grid\s+quality", text, re.I):
            return None
        comparative = re.search(
            r"两者|对比|跨领域|computational.{0,20}visual|visual.{0,20}computational",
            text,
            re.I,
        )
        if comparative:
            return "comparative"
        visual = re.search(
            r"视觉|感知|纹理|着色|压缩|3d\s*(?:graphics?|model)|"
            r"visual|perceptual|textured|colored\s+mesh|multimedia",
            text,
            re.I,
        )
        numerical = re.search(
            r"数值计算|数值模拟|工程仿真|有限元|有限体积|偏微分|"
            r"cfd|fea|cae|finite\s+(?:element|volume)|numerical|solver|discretization|"
            r"jacobian|skewness|orthogonality|virtual\s+element",
            text,
            re.I,
        )
        if visual and not numerical:
            return "visual"
        # A bare "mesh quality" request is ambiguous. The workflow clarification
        # gate asks the user; direct Agent requests default to numerical simulation,
        # which is the engineering meaning used by EvoAgent's mesh knowledge bases.
        return "computational"

    def should_research(self, task: str) -> bool:
        local_path = re.search(
            r"桌面|本地|文件夹|目录|磁盘|硬盘|工作区|文档|下载|"
            r"[A-Za-z]:[\\/]|(?:^|\s)[.~]{1,2}[\\/]",
            task,
            re.I,
        )
        local_action = re.search(
            r"读取|打开|查看|列出|浏览|找出|查找|搜索|写入|保存|修改|删除|执行|"
            r"read|open|list|browse|find|search|write|save|edit|run|exec",
            task,
            re.I,
        )
        if local_path and local_action:
            return False
        return research_routing_service.should_research(task)

    def research_mode(self, task: str) -> str:
        return research_routing_service.classify(task).mode

    def research_domain(self, task: str) -> str:
        return research_routing_service.classify(task).domain

    def explicit_source_count(self, task: str) -> int | None:
        """Return the user's preferred evidence target, not a hard success gate."""

        request = self.research_request(task)
        patterns = (
            r"(?:至少|不少于|约|大约|检索|搜寻|纳入|包含|覆盖)?\s*(\d{1,3})\s*(?:篇|条)\s*(?:文献|论文|资料)?",
            r"(\d{1,3})\s*(?:papers?|articles?|references?|studies)",
        )
        for pattern in patterns:
            match = re.search(pattern, request, re.I)
            if match:
                return max(5, min(int(match.group(1)), 80))
        return None

    def requested_source_count(self, task: str) -> int:
        """Return a bounded evidence target for research tasks."""

        return self.explicit_source_count(task) or 12

    @classmethod
    def requested_year_range(cls, task: str) -> tuple[int, int] | None:
        request = cls.research_request(task)
        current_year = date.today().year
        explicit = re.search(
            r"\b(19\d{2}|20\d{2})\s*(?:[-–—~至到]|to)\s*(19\d{2}|20\d{2})\b",
            request,
            re.I,
        )
        if explicit:
            start, end = sorted((int(explicit.group(1)), int(explicit.group(2))))
            return max(1900, start), min(current_year + 1, end)
        recent = re.search(
            r"(?:(?:近|最近|过去)\s*(\d{1,2})\s*年|(?:recent|past)\s+(\d{1,2})\s+years?)",
            request,
            re.I,
        )
        if recent:
            years = max(1, min(int(recent.group(1) or recent.group(2)), 50))
            return current_year - years, current_year
        since = re.search(r"(?:自|从)\s*(19\d{2}|20\d{2})\s*年?(?:以来|起)", request)
        if since:
            return int(since.group(1)), current_year
        return None

    @staticmethod
    def research_request(task: str) -> str:
        """Keep user-confirmed constraints while excluding upstream node content."""

        match = re.search(
            r"【用户原始意图】\s*([\s\S]*?)(?=\n\s*【当前工作流节点】|$)",
            task,
            re.I,
        )
        if match and match.group(1).strip():
            return match.group(1).strip()[:8000]
        value = re.split(
            r"\n\s*【(?:当前工作流节点|本节点收到的输入|执行约束|节点专用任务说明)】",
            task,
            maxsplit=1,
        )[0]
        return value.strip()[:8000]

    @staticmethod
    def research_subject(task: str) -> str:
        """Extract the user's topic from workflow wrappers before building search queries."""

        for pattern in (
            r"【用户原始意图】\s*([\s\S]*?)(?=\n\s*【|$)",
            r"(?:^|\n)原始任务\s*[：:]\s*([\s\S]*?)(?=\n\s*【|\n\s*\n|$)",
        ):
            match = re.search(pattern, task, re.I)
            if match and match.group(1).strip():
                return " ".join(match.group(1).split())[:400]
        before_metadata = re.split(r"\n\s*【", task, maxsplit=1)[0].strip()
        first_line = next(
            (line.strip() for line in before_metadata.splitlines() if line.strip()),
            task.strip(),
        )
        return " ".join(first_line.split())[:400]

    @staticmethod
    def normalized_research_subject(subject: str) -> str:
        """Remove workflow/delivery scaffolding while preserving the research topic."""

        value = " ".join(str(subject or "").split()).strip(" ：:，,。；;、")
        value = re.sub(
            r"^(?:(?:请|麻烦|帮我|请帮我|给我|我想|我需要|需要|想要)\s*)?"
            r"(?:(?:新建|创建|构建|设计|生成|制作|编排|启动)\s*"
            r"(?:一个|一份|一套|新的?)?\s*(?:智能|自动)?\s*工作流\s*"
            r"(?:来|用于|用来|以便)?\s*[,，:：、-]?\s*)+",
            "",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"^(?:(?:请|麻烦|帮我|请帮我|给我|我想|我需要|需要|想要)\s*)?"
            r"(?:查找|检索|搜索|查询|调查|调研|研究|了解|整理|汇总)\s*",
            "",
            value,
            flags=re.I,
        )
        value = re.sub(r"^(?:关于|围绕|针对|有关|就)\s*", "", value, flags=re.I)
        value = re.sub(
            r"^(?:please\s+)?(?:review|search|find|research|investigate|summarize)\s+",
            "",
            value,
            flags=re.I,
        )
        # Delivery instructions describe what to do with evidence, not what to
        # search for. Cutting them before query construction keeps scholarly
        # indexes from receiving the whole workflow command.
        value = re.split(
            r"(?:[,，;；。、]\s*|\s+)(?:并|然后|再|最后)?\s*"
            r"(?:写|撰写|生成|输出|整理|制作|形成|改写|翻译|投稿|交付)\s*"
            r"(?:为|成)?\s*(?:一篇|一份|一版)?\s*"
            r"(?:中文|英文|中英文|SCI|学术|期刊|会议)?",
            value,
            maxsplit=1,
            flags=re.I,
        )[0]
        value = re.sub(
            r"(?:相关|有关|方向)?\s*(?:的)?\s*"
            r"(?:SCI\s*)?(?:论文|文献|文章|期刊|资料|研究进展|文献综述|系统综述|综述)"
            r"(?:\s*(?:检索|搜索|调研|调查|写作|撰写|生成|整理|汇总))?.*$",
            "",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"(?:近|最近|过去)\s*\d{1,2}\s*年|"
            r"\b(?:19\d{2}|20\d{2})\s*(?:[-–—~至到]|to)\s*(?:19\d{2}|20\d{2})\b|"
            r"(?:至少|不少于|约|大约|检索|搜寻|纳入|包含|覆盖)?\s*"
            r"\d{1,3}\s*(?:篇|条)\s*(?:文献|论文|资料)?",
            " ",
            value,
            flags=re.I,
        )
        value = re.sub(
            r"\b(?:write|create|generate)\s+(?:an?\s+)?(?:sci|review)\b.*$", "", value, flags=re.I
        )
        return " ".join(value.split()).strip(" ：:，,。；;、-–—")[:180]

    @staticmethod
    def _finance_query_variants(request: str) -> list[str]:
        """Build evidence-oriented market queries instead of searching the request verbatim."""

        market_match = re.search(
            r"A股|港股|美股|沪深(?:两市)?|创业板|科创板|北交所|"
            r"S&P\s*500|Nasdaq|NYSE|Hong\s+Kong\s+stocks?",
            request,
            re.I,
        )
        market = market_match.group(0) if market_match else "证券市场"
        price_match = re.search(
            r"(?:价格|股价)?\s*(?:区间)?\s*(?:在|低于|不高于|小于|≤)?\s*"
            r"(\d+(?:\.\d+)?)\s*元\s*(?:以下|以内|之内)?",
            request,
            re.I,
        )
        price = f"{price_match.group(1)}元以下" if price_match else ""
        constraint = " ".join(item for item in (market, price) if item)
        theme_match = re.search(
            r"(?:围绕|关注|看好|研究|分析)\s*([^，。；;]{2,24}?)(?:行业|板块|产业)",
            request,
            re.I,
        )
        theme = f"{theme_match.group(1)}行业" if theme_match else "行业板块"
        return [
            f"{market} {theme} 景气度 政策催化 最新",
            f"{constraint} 上市公司 业绩增长 估值 现金流 最新",
            f"site:cninfo.com.cn {market} 业绩预告 机构调研 公告",
            f"site:sse.com.cn OR site:szse.cn {market} 上市公司公告 风险提示",
            f"{market} 行业轮动 资金流向 市场表现 最新",
            f"{constraint} 减持 监管 退市 流动性 风险",
        ]

    @staticmethod
    def _domain_web_queries(base_query: str, domain: str) -> list[str]:
        facets: dict[str, tuple[str, str, str]] = {
            "news_current": ("事件时间线 最新进展", "当事方 官方声明", "事实核查 多来源"),
            "policy_government": (
                "site:gov.cn 政策 原文",
                "主管部门 官方解读",
                "生效时间 适用范围",
            ),
            "legal": ("现行法律 法条 官方", "法院 司法解释 案例", "合规风险 生效状态"),
            "health_medical": ("临床指南 共识", "卫生部门 权威机构", "证据等级 风险 禁忌"),
            "product_comparison": ("官方规格 当前价格", "独立评测 实测", "售后 长期使用 反馈"),
            "travel_local": ("官方 开放时间 预约", "实时交通 天气", "当前价格 安全 提示"),
            "education": ("课程标准 官方", "教学实践 案例", "评价方法 学习效果"),
        }
        suffixes = facets.get(domain)
        if not suffixes:
            return [
                base_query,
                f"{base_query} 官方 权威来源",
                f"{base_query} 最新 数据 报告",
                f"{base_query} 背景 现状",
            ]
        return [base_query, *(f"{base_query} {suffix}" for suffix in suffixes)]

    def institution_name(self, task: str) -> str | None:
        match = re.search(r"([\u4e00-\u9fff]{2,24}(?:大学|学院))", task)
        if not match:
            return None
        value = re.sub(
            r"^(?:(?:帮我|请|对|想要|需要|调查|调研|查询|查找|了解))+",
            "",
            match.group(1),
        )
        return value or None

    @staticmethod
    def generalized_academic_queries(topic: str) -> list[str]:
        """Build domain-independent recall facets for any scholarly topic."""

        base = " ".join(str(topic or "").split()).strip(' ：:，,。；;、-–—"')[:180]
        if not base:
            return []
        facets = (
            ("文献综述", "研究进展", "方法", "评价", "应用")
            if re.search(r"[\u4e00-\u9fff]", base)
            else (
                "systematic review",
                "recent advances",
                "methods",
                "benchmark evaluation",
                "applications",
            )
        )
        return [f'"{base}"', *(f'"{base}" "{facet}"' for facet in facets)]

    def query_variants(self, task: str) -> list[str]:
        request = self.research_request(task)
        route = research_routing_service.classify(request)
        mode = route.mode
        subject = self.research_subject(task)
        normalized_subject = self.normalized_research_subject(subject)
        mesh_domain = self.mesh_domain(subject)
        institution = self.institution_name(request)
        if institution and mode == "web":
            return [
                f'"{institution}" 官网 招生',
                f'"{institution}" 师资 学科专业',
                f'"{institution}" 就业质量报告 录取分数',
                f'"{institution}" 校园生活 宿舍',
            ]

        cleaned = re.sub(
            r"帮我|给我|请|关于|完成|总结|综述|调查|调研|查询|查找",
            " ",
            normalized_subject or subject,
            flags=re.I,
        )
        cleaned = re.sub(
            r"^(?:围绕|针对|有关|就)\s*|(?:给|写|生成|提供|撰写)\s*一(?:份|篇)",
            " ",
            cleaned,
        )
        cleaned = cleaned.replace("的", " ")
        cleaned = " ".join(cleaned.split()).strip(" ：:，,。")
        translations = {
            "二维人体姿态估计": "2D human pose estimation",
            "三维人体姿态估计": "3D human pose estimation",
            "单人体姿态估计": "single-person human pose estimation",
            "多人体姿态估计": "multi-person human pose estimation",
            "人体姿态估计": "human pose estimation",
            "二维": "2D",
            "三维": "3D",
            "结构化网格": "structured grid mesh",
            "网格质量": "mesh quality",
            "质量评估": "quality assessment",
            "评估": "assessment",
            "有限元": "finite element",
        }
        english = cleaned
        for source, target in translations.items():
            english = english.replace(source, f" {target} ")
        english = " ".join(english.split())
        values: list[str] = []
        if english != cleaned and re.search(r"[a-z]", english, re.I):
            # Search the disambiguated English terminology first. Scholarly indexes
            # otherwise tend to match broad Chinese words such as “结构/质量/评估”.
            values.append(english)
            if "structured" in english.lower() and "mesh" in english.lower():
                values.extend(
                    [
                        "2D structured mesh quality metrics Jacobian skewness orthogonality",
                        "2D structured grid mesh quality evaluation CFD",
                    ]
                )
        base_query = cleaned or subject
        if mode == "academic":
            academic_base = english if re.search(r"[a-z]", english, re.I) else base_query
            # Prefer concise English scholarly terms when we could translate the
            # subject. Sending the Chinese workflow sentence as a second query
            # reduces precision and can consume one of the four provider slots.
            if academic_base == base_query:
                values.append(base_query)
            if re.search(r"human\s+pose\s+estimation|\bhpe\b", academic_base, re.I):
                canonical = "human pose estimation"
                modifiers = " ".join(
                    item
                    for item in ("2D", "3D", "multi-person", "single-person", "monocular")
                    if item.lower() in academic_base.lower()
                )
                primary = f'"{" ".join(part for part in (modifiers, canonical) if part)}"'
                values = [
                    primary,
                    f'"{canonical}" "survey"',
                    f'"{canonical}" "deep learning"',
                    f'"{canonical}" "transformer"',
                    f'"{canonical}" "benchmark dataset"',
                    f'"{canonical}" "applications"',
                ]
            elif re.search(
                r"(?:mesh|grid).*(?:quality|assessment)|(?:quality|assessment).*(?:mesh|grid)",
                academic_base,
                re.I,
            ):
                if mesh_domain == "computational":
                    # Never use a bare "mesh quality assessment" query here: it is
                    # dominated by perceptual quality papers about textured 3D media.
                    if "structured" in academic_base.lower():
                        values = [
                            '"2D structured mesh" "quality evaluation"'
                            if "2d" in academic_base.lower()
                            else '"structured mesh" "quality evaluation"',
                            '"structured grid" "quality assessment"',
                            '"structured mesh" "quality metrics"',
                            '"structured grid" "quality indicators"',
                            '"computational mesh" "quality metrics"',
                            '"mesh quality" "numerical simulation"',
                        ]
                    else:
                        values = [
                            '"mesh quality" "assessment"',
                            '"mesh quality" "metrics"',
                            '"unstructured mesh" "quality"',
                            '"mesh quality" "indicators"',
                            '"computational mesh" "quality"',
                            '"mesh quality" "numerical simulation"',
                        ]
                elif mesh_domain == "visual":
                    values = [
                        '"3D mesh visual quality assessment"',
                        '"textured mesh quality assessment"',
                        '"no-reference mesh quality"',
                        '"colored mesh quality"',
                        '"3D mesh visual quality" "benchmark"',
                        '"mesh perceptual quality" "dataset"',
                    ]
                elif mesh_domain == "comparative":
                    values = [
                        '"mesh quality indicators"',
                        '"mesh quality metrics"',
                        '"3D mesh visual quality assessment"',
                        '"textured mesh quality assessment"',
                        '"computational mesh quality" "evaluation"',
                        '"mesh perceptual quality" "evaluation"',
                    ]
                else:
                    values = self.generalized_academic_queries(academic_base)
            else:
                values = self.generalized_academic_queries(academic_base)
        elif route.domain == "finance_markets":
            values = self._finance_query_variants(request)
        else:
            values = self._domain_web_queries(base_query, route.domain)
        # A quoted academic phrase is already the complete retrieval concept.
        # Never leave discovery modifiers after its closing quote: every provider
        # and the visible browser panel must execute exactly the same phrase.
        if mode == "academic":
            values = [self.exact_quoted_query(value) for value in values]
        result_limit = 6 if mode == "academic" or route.domain == "finance_markets" else 4
        return list(dict.fromkeys(values))[:result_limit]

    @staticmethod
    def exact_quoted_query(query: str) -> str:
        """Drop unquoted suffixes while preserving independent phrase groups."""

        value = " ".join(str(query or "").split()).strip()
        quoted = [
            " ".join(part.split()).strip()
            for part in re.findall(r'["“]([^"”]+)["”]', value)
            if part.strip()
        ]
        if not quoted:
            return value[:300]
        return " ".join(f'"{part}"' for part in dict.fromkeys(quoted))[:300]

    @staticmethod
    def crossref_query(query: str) -> str:
        """Use explicit quoted concepts as Crossref's bibliographic query.

        Query variants may append discovery modifiers for a web search engine,
        for example ``"structured grid quality" finite volume solver evaluation``.
        Crossref treats every appended word as bibliographic evidence, which can
        dilute an otherwise precise concept search.  When the variant contains
        quoted phrases, those phrases are therefore the complete Crossref query.
        """

        value = " ".join(str(query or "").split()).strip()
        quoted = [
            " ".join(part.split()).strip()
            for part in re.findall(r'["“]([^"”]+)["”]', value)
            if part.strip()
        ]
        if quoted:
            return " ".join(dict.fromkeys(quoted))[:300]
        return value[:300]

    async def collect(self, task: str, on_event: EventHandler) -> list[dict[str, Any]]:
        request = self.research_request(task)
        subject = self.research_subject(task)
        route = research_routing_service.classify(request)
        mode = route.mode
        queries = self.query_variants(task)
        target_sources = self.requested_source_count(task) if mode == "academic" else 12
        year_range = self.requested_year_range(task) if mode == "academic" else None
        mesh_domain = self.mesh_domain(subject)
        await on_event(
            {
                "type": "research_planning",
                "queries": queries,
                "strategy": "comprehensive",
                "mode": mode,
                "target_sources": target_sources,
                "year_range": list(year_range) if year_range else None,
                "research_scope": mesh_domain,
                "domain": route.domain,
                "domain_label": route.domain_label,
                "preferred_sources": list(route.preferred_sources),
                "source_strategy": route.guidance,
                "high_stakes": route.high_stakes,
            }
        )
        candidates: list[dict[str, Any]] = []
        verification_offered = False
        used_crossref_queries: set[str] = set()
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "EvoAgent/0.1 academic-research"},
        ) as client:
            for query in queries:
                crossref_query = self.crossref_query(query)
                crossref_query_key = crossref_query.casefold()
                include_crossref = (
                    mode == "academic" and crossref_query_key not in used_crossref_queries
                )
                if include_crossref:
                    used_crossref_queries.add(crossref_query_key)
                search_url = (
                    self.scholar_search_url(query)
                    if mode == "academic"
                    else self.web_search_url(query)
                )
                provider_urls = (
                    [
                        {"provider": "Google Scholar", "query": query, "url": search_url},
                        *(
                            [
                                {
                                    "provider": "Crossref",
                                    "query": crossref_query,
                                    "url": (
                                        "https://api.crossref.org/works?query.bibliographic="
                                        f"{quote_plus(crossref_query)}"
                                    ),
                                }
                            ]
                            if include_crossref
                            else []
                        ),
                    ]
                    if mode == "academic"
                    else [
                        {"provider": "360 Web Search", "url": self.web_search_url(query)},
                        {
                            "provider": "DuckDuckGo",
                            "url": f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
                        },
                        {
                            "provider": "Bing",
                            "url": f"https://www.bing.com/search?q={quote_plus(query)}",
                        },
                    ]
                )
                provider_queries = {
                    str(item["provider"]): str(item.get("query") or query) for item in provider_urls
                }
                await on_event(
                    {
                        "type": "web_search_started",
                        "query": query,
                        "mode": mode,
                        "search_url": search_url,
                        "search_label": (
                            "Google Scholar 学术检索" if mode == "academic" else "普通网页检索"
                        ),
                        "scholar_url": search_url if mode == "academic" else None,
                        "provider_urls": provider_urls,
                        "provider_queries": provider_queries,
                    }
                )
                results, provider_errors = await self._search(
                    client,
                    query,
                    mode,
                    target_sources,
                    year_range=year_range,
                    crossref_query=crossref_query,
                    include_crossref=include_crossref,
                )
                for provider_error in provider_errors:
                    provider_target = next(
                        (
                            item["url"]
                            for item in provider_urls
                            if item["provider"] == provider_error.get("provider")
                        ),
                        search_url,
                    )
                    provider_error["provider_url"] = provider_target
                    if provider_error.get("verification_required") and not verification_offered:
                        verification_offered = True
                        verification_id = self._begin_verification(
                            provider=str(provider_error.get("provider") or "Google Scholar"),
                            url=str(provider_error.get("verification_url") or provider_target),
                            query=query,
                        )
                        provider_error["verification_id"] = verification_id
                        provider_error["verification_wait_seconds"] = self.verification_wait_seconds
                    await on_event(
                        {
                            "type": "web_search_provider_error",
                            "query": query,
                            **provider_error,
                        }
                    )
                    verification_id = str(provider_error.get("verification_id") or "")
                    if verification_id:
                        await on_event(
                            {
                                "type": "human_verification_required",
                                "verification_id": verification_id,
                                "provider": provider_error.get("provider"),
                                "query": query,
                                "url": provider_error.get("verification_url") or search_url,
                                "wait_seconds": self.verification_wait_seconds,
                                "message": "检索站点要求机器人验证，工作流已等待用户处理。",
                            }
                        )
                        decision = await self._wait_for_verification(verification_id)
                        if decision.get("approved"):
                            await on_event(
                                {
                                    "type": "human_verification_retrying",
                                    "verification_id": verification_id,
                                    "provider": provider_error.get("provider"),
                                    "query": query,
                                }
                            )
                            try:
                                retry_results = await self._search_google_scholar(client, query)
                                results.extend(retry_results)
                                await on_event(
                                    {
                                        "type": "human_verification_completed",
                                        "verification_id": verification_id,
                                        "provider": provider_error.get("provider"),
                                        "query": query,
                                        "count": len(retry_results),
                                    }
                                )
                            except Exception as exc:
                                await on_event(
                                    {
                                        "type": "human_verification_retry_failed",
                                        "verification_id": verification_id,
                                        "provider": provider_error.get("provider"),
                                        "query": query,
                                        "error": str(exc)[:240],
                                    }
                                )
                        else:
                            await on_event(
                                {
                                    "type": "human_verification_skipped"
                                    if decision.get("skipped")
                                    else "human_verification_timed_out",
                                    "verification_id": verification_id,
                                    "provider": provider_error.get("provider"),
                                    "query": query,
                                    "message": "已改用其他检索源继续，不会伪造缺失资料。",
                                }
                            )
                relevant = self._rank_results(task, results)
                await on_event(
                    {
                        "type": "web_search_results",
                        "query": query,
                        "mode": mode,
                        "provider_queries": provider_queries,
                        "count": len(relevant),
                        "discarded": len(results) - len(relevant),
                        "results": [
                            {
                                "title": item["title"],
                                "url": item["url"],
                                "scholar_url": item.get("scholar_url"),
                                "source": item.get("source", "Web"),
                                "doi": item.get("doi"),
                                "published_year": item.get("published_year"),
                                "authors": item.get("authors", []),
                                "venue": item.get("venue"),
                                "relevance": item.get("relevance", 0),
                                "credibility": item.get("credibility"),
                            }
                            for item in relevant[:10]
                        ],
                    }
                )
                candidates.extend(relevant)

            # The requested count is a preferred target. Keep additional relevant
            # records when available, while bounding evidence context and UI size.
            selection_limit = min(80, max(target_sources, target_sources * 2))
            sources = self._rank_results(task, candidates)[:selection_limit]
            current_year = date.today().year
            await on_event(
                {
                    "type": "research_sources_selected",
                    "count": len(sources),
                    "research_scope": mesh_domain,
                    "recent_3_year_count": sum(
                        1
                        for item in sources
                        if item.get("published_year")
                        and int(item["published_year"]) >= current_year - 2
                    ),
                    "recent_5_year_count": sum(
                        1
                        for item in sources
                        if item.get("published_year")
                        and int(item["published_year"]) >= current_year - 4
                    ),
                    "providers": sorted({str(item.get("source") or "Web") for item in sources}),
                    "results": [
                        {
                            "title": item["title"],
                            "url": item["url"],
                            "source": item.get("source", "Web"),
                            "mode": mode,
                            "scholar_url": item.get("scholar_url"),
                            "doi": item.get("doi"),
                            "published_year": item.get("published_year"),
                            "authors": item.get("authors", []),
                            "venue": item.get("venue"),
                            "relevance": item.get("relevance", 0),
                            "credibility": item.get("credibility"),
                        }
                        for item in sources
                    ],
                }
            )

            async def fetch_one(index: int, source: dict[str, Any]):
                content, status = await self._fetch_page(client, source["url"])
                if not content:
                    content = source.get("description", "")
                    if content:
                        status = "search-snippet"
                return index, {**source, "content": content[:6000], "status": status}

            fetch_tasks = []
            fetch_limit = min(len(sources), 20 if mode == "academic" else 10)
            for index, source in enumerate(sources[:fetch_limit], 1):
                await on_event(
                    {
                        "type": "web_fetch_started",
                        "index": index,
                        "url": source["url"],
                        "title": source["title"],
                        "source": source.get("source", "Web"),
                        "doi": source.get("doi"),
                        "published_year": source.get("published_year"),
                        "scholar_url": source.get("scholar_url"),
                        "credibility": source.get("credibility"),
                    }
                )
                fetch_tasks.append(asyncio.create_task(fetch_one(index, source)))

            enriched_by_index: dict[int, dict[str, Any]] = {}
            for future in asyncio.as_completed(fetch_tasks):
                index, item = await future
                enriched_by_index[index] = item
                await on_event(
                    {
                        "type": "web_page_fetched",
                        "index": index,
                        "url": item["url"],
                        "title": item["title"],
                        "source": item.get("source", "Web"),
                        "doi": item.get("doi"),
                        "published_year": item.get("published_year"),
                        "scholar_url": item.get("scholar_url"),
                        "credibility": item.get("credibility"),
                        "status": item["status"],
                        "content_excerpt": item["content"][:900],
                    }
                )
            enriched = []
            for index, source in enumerate(sources, 1):
                enriched.append(
                    enriched_by_index.get(
                        index,
                        {
                            **source,
                            "content": source.get("description", ""),
                            "status": "metadata-only",
                        },
                    )
                )
        return enriched

    def _begin_verification(self, *, provider: str, url: str, query: str) -> str:
        verification_id = str(uuid4())
        self._verification_sessions[verification_id] = {
            "id": verification_id,
            "provider": provider,
            "url": url,
            "query": query,
            "event": asyncio.Event(),
            "approved": False,
            "skipped": False,
        }
        return verification_id

    async def _wait_for_verification(self, verification_id: str) -> dict[str, Any]:
        session = self._verification_sessions.get(verification_id)
        if not session:
            return {"approved": False, "skipped": True}
        try:
            await asyncio.wait_for(
                session["event"].wait(),
                timeout=self.verification_wait_seconds,
            )
            return {
                "approved": bool(session.get("approved")),
                "skipped": bool(session.get("skipped")),
            }
        except TimeoutError:
            return {"approved": False, "skipped": False}
        finally:
            self._verification_sessions.pop(verification_id, None)

    def active_verifications(self) -> list[dict[str, Any]]:
        return [
            {
                "verification_id": key,
                "provider": value["provider"],
                "url": value["url"],
                "query": value["query"],
            }
            for key, value in self._verification_sessions.items()
        ]

    def complete_verification(
        self,
        verification_id: str,
        *,
        approved: bool,
        url: str,
        cookies: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session = self._verification_sessions.get(verification_id)
        if not session:
            raise LookupError("该人工验证已经过期或已经处理")
        expected_host = (urlparse(str(session["url"])).hostname or "").lower()
        submitted_host = (urlparse(url).hostname or "").lower()
        if approved and expected_host != submitted_host:
            raise ValueError("验证网页与待处理检索站点不匹配")

        accepted_cookies: list[str] = []
        if approved and submitted_host.endswith("google.com"):
            for item in cookies[:80]:
                name = str(item.get("name") or "")
                value = str(item.get("value") or "")
                if name in self.safe_scholar_cookie_names and value:
                    accepted_cookies.append(f"{name}={value}")
            if accepted_cookies:
                # Session-only: never written to the database, trace or logs.
                self._scholar_cookie_header = "; ".join(accepted_cookies)
        session["approved"] = approved
        session["skipped"] = not approved
        session["event"].set()
        return {
            "accepted": True,
            "approved": approved,
            "cookie_names": [item.split("=", 1)[0] for item in accepted_cookies],
        }

    def _rank_results(self, task: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        subject = self.normalized_research_subject(self.research_subject(task))
        task_lower = (subject or task).lower()
        request = self.research_request(task)
        route = research_routing_service.classify(request)
        scope_lower = request.lower()
        mesh_domain = self.mesh_domain(subject)
        year_range = self.requested_year_range(task)
        groups: list[tuple[str, list[str], int, bool]] = []
        institution = self.institution_name(request)
        if institution:
            groups.append(("institution", [institution.lower()], 8, True))
        if re.search(r"(?<![a-z0-9])2d(?![a-z0-9])|二维", scope_lower):
            groups.append(("dimension", ["2d", "two-dimensional", "二维"], 4, False))
        if re.search(r"结构化网格|structured\s+(grid|mesh)", scope_lower):
            groups.append(
                (
                    "structured_mesh",
                    ["structured mesh", "structured grid", "block structured", "结构化网格"],
                    5,
                    False,
                )
            )
        if re.search(r"网格|\bmesh\b|\bgrid\b", scope_lower):
            groups.append(("mesh", ["mesh", "grid", "网格"], 4, True))
        if re.search(r"质量|评估|quality|assessment|evaluation", scope_lower):
            groups.append(
                (
                    "quality",
                    [
                        "quality",
                        "assessment",
                        "evaluation",
                        "metric",
                        "indicator",
                        "improvement",
                        "distortion",
                        "validity",
                        "质量",
                        "评估",
                    ],
                    3,
                    mesh_domain is not None,
                )
            )
        if re.search(r"人体姿态估计|human\s+pose\s+estimation|\bhpe\b", scope_lower, re.I):
            groups.extend(
                [
                    (
                        "pose_estimation",
                        [
                            "pose estimation",
                            "pose-estimation",
                            "2d pose",
                            "3d pose",
                            "keypoint detection",
                            "人体姿态估计",
                        ],
                        6,
                        True,
                    ),
                    (
                        "human_body",
                        [
                            "human",
                            "person",
                            "body",
                            "skeleton",
                            "multi-person",
                            "人体",
                        ],
                        5,
                        True,
                    ),
                ]
            )
        if route.domain == "finance_markets":
            groups.extend(
                [
                    (
                        "finance_market",
                        [
                            "a股",
                            "股票",
                            "个股",
                            "证券",
                            "上市公司",
                            "上证",
                            "深证",
                            "stock",
                            "equity",
                            "shares",
                        ],
                        7,
                        False,
                    ),
                    (
                        "fundamentals",
                        [
                            "财报",
                            "业绩",
                            "营收",
                            "净利润",
                            "现金流",
                            "估值",
                            "市盈率",
                            "earnings",
                            "revenue",
                            "cash flow",
                            "valuation",
                        ],
                        4,
                        False,
                    ),
                    (
                        "sector_market",
                        ["板块", "行业", "产业", "景气", "资金流向", "sector", "industry"],
                        3,
                        False,
                    ),
                    (
                        "market_risk",
                        ["风险", "减持", "处罚", "退市", "诉讼", "质押", "risk", "delisting"],
                        3,
                        False,
                    ),
                ]
            )

        ranked: dict[str, dict[str, Any]] = {}
        for item in results:
            text = html.unescape(f"{item.get('title', '')} {item.get('description', '')}").lower()
            text = re.sub(r"[‐‑‒–—−]", "-", text)
            computational_terms = (
                "finite element",
                "finite volume",
                "virtual element",
                "computational fluid",
                "numerical simulation",
                "numerical analysis",
                "discretization",
                "jacobian",
                "skewness",
                "orthogonality",
                "aspect ratio",
                "element quality",
                "mesh generation",
                "mesh optimization",
                "mesh quality improvement",
                "mesh smoothing",
                "mesh untangling",
                "unstructured mesh",
                "structured mesh",
                "structured grid",
                "adaptive mesh",
                "anisotropic mesh",
                "solver",
                " cfd",
                " fea",
                " cae",
                "有限元",
                "有限体积",
                "数值模拟",
                "网格生成",
            )
            visual_terms = (
                "visual quality",
                "perceptual quality",
                "textured mesh",
                "colored mesh",
                "blind mesh",
                "no-reference",
                "full-reference",
                "subjective quality",
                "visual saliency",
                "compression",
                "multimedia",
                "视觉质量",
                "感知质量",
                "纹理网格",
            )
            medical_terms = (
                "surgical mesh",
                "hernia",
                "urethral",
                "pelvic",
                "prosthetic mesh",
                "mesh complication",
            )
            computational_mesh_noise = (
                "rebar mesh",
                "wire mesh",
                "mesh dome",
                "mesh-free",
                "meshfree",
                "screen mesh",
                "filter mesh",
                "stent mesh",
                "eeg head model",
                "electroencephalography",
            )
            computational_hit = any(term in text for term in computational_terms)
            visual_hit = any(term in text for term in visual_terms)
            has_mesh_term = bool(re.search(r"\b(?:mesh|grid|element)s?\b", text, re.I))
            mesh_quality_context_hit = bool(
                re.search(
                    r"\b(?:mesh|grid|element)[ -](?:quality|metric|indicator|assessment|evaluation)s?\b"
                    r"|\bquality(?: assessment| evaluation)?(?: of| for)? (?:a |the )?"
                    r"(?:structured |unstructured )?(?:mesh|grid|element)s?\b",
                    text,
                    re.I,
                )
                or (
                    has_mesh_term
                    and re.search(
                        r"\b(?:jacobian|skewness|orthogonality|aspect ratio|"
                        r"mesh distortion|discretization error)\b",
                        text,
                        re.I,
                    )
                )
            )
            if mesh_domain in {"computational", "comparative"} and any(
                term in text for term in medical_terms
            ):
                continue
            if mesh_domain == "computational" and not computational_hit:
                continue
            if mesh_domain == "computational" and not mesh_quality_context_hit:
                # A paper that merely uses a structured grid (for example an
                # urban-flood solver) is not evidence about grid quality itself.
                continue
            if mesh_domain == "computational" and any(
                term in text for term in computational_mesh_noise
            ):
                continue
            if mesh_domain == "visual" and not visual_hit:
                continue
            if mesh_domain == "computational" and visual_hit and not computational_hit:
                continue
            published_year = item.get("published_year")
            if year_range and not published_year:
                # An undated record cannot prove compliance with an explicit
                # publication window and must not consume the requested quota.
                continue
            if year_range and published_year:
                try:
                    year = int(published_year)
                except (TypeError, ValueError):
                    year = None
                if year is not None and not year_range[0] <= year <= year_range[1]:
                    continue
            matched: list[str] = []
            score = 2 if item.get("source") == "Crossref" else 0
            required_ok = True
            for name, terms, weight, required in groups:
                hit = any(term in text for term in terms)
                if hit:
                    matched.append(name)
                    score += weight
                elif required:
                    required_ok = False
            if groups and not required_ok:
                continue
            if route.domain == "finance_markets" and "finance_market" not in matched:
                continue
            if computational_hit:
                matched.append("computational_mesh")
                score += 7
            if visual_hit:
                matched.append("visual_mesh")
                score += 5
            if year_range and published_year:
                year = int(published_year)
                span = max(1, year_range[1] - year_range[0])
                score += max(0, min(5, round((year - year_range[0]) / span * 5)))
            if any(name == "quality" for name, *_rest in groups) and "quality" not in matched:
                # Background work on structured mesh generation is useful, but ranks
                # below papers that explicitly discuss metrics/evaluation.
                score -= 2
            hostname = (urlparse(str(item.get("url", ""))).hostname or "").lower()
            if route.domain == "finance_markets":
                if hostname.endswith(
                    (
                        "cninfo.com.cn",
                        "sse.com.cn",
                        "szse.cn",
                        "bse.cn",
                        "csrc.gov.cn",
                        "gov.cn",
                    )
                ):
                    score += 8
                    matched.append("official_source")
                elif hostname.endswith(("eastmoney.com", "cnstock.com", "stcn.com")):
                    score += 3
            if institution and hostname.endswith((".edu.cn", ".edu", ".gov.cn", ".gov")):
                score += 6
            if not groups:
                tokens = {
                    token
                    for token in re.findall(r"[a-z0-9]{3,}", task_lower)
                    if token not in {"review", "survey", "search", "research"}
                }
                chinese = re.sub(
                    r"请|帮我|给我|我想|我需要|需要|想要|查找|检索|搜索|查询|"
                    r"调查|调研|研究|了解|整理|汇总|最新|相关|关于|进行|一份|一个",
                    " ",
                    task_lower,
                )
                for run in re.findall(r"[\u4e00-\u9fff]{2,}", chinese):
                    if len(run) <= 6:
                        tokens.add(run)
                    else:
                        tokens.update(
                            run[index : index + size]
                            for size in (2, 3, 4)
                            for index in range(0, len(run) - size + 1)
                        )
                overlap = sum(token in text for token in tokens)
                if not overlap:
                    continue
                score += overlap
            title_key = re.sub(r"[^a-z0-9一-鿿]+", "", str(item.get("title") or "").lower())
            enriched = {**item, "relevance": score, "matched_concepts": matched}
            key = title_key or str(item.get("doi") or item.get("url", "")).lower()
            current = ranked.get(key)
            if current is None or score > current.get("relevance", 0):
                ranked[key] = enriched
        return sorted(ranked.values(), key=lambda item: item.get("relevance", 0), reverse=True)

    async def _search(
        self,
        client: httpx.AsyncClient,
        query: str,
        mode: str,
        target_sources: int = 12,
        *,
        year_range: tuple[int, int] | None = None,
        crossref_query: str | None = None,
        include_crossref: bool = True,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if mode == "academic":
            providers = [("Google Scholar", self._search_google_scholar(client, query))]
            if include_crossref:
                providers.append(
                    (
                        "Crossref",
                        self._search_crossref(
                            client,
                            crossref_query or self.crossref_query(query),
                            rows=max(20, min(target_sources * 2, 80)),
                            year_range=year_range,
                        ),
                    )
                )
        else:
            providers = [
                ("360 Web Search", self._search_360(client, query)),
                ("DuckDuckGo", self._search_duckduckgo(client, query)),
                ("Bing", self._search_bing(client, query)),
            ]
        # Individual provider failures do not suppress the remaining providers.
        batches = await asyncio.gather(
            *(task for _name, task in providers),
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen: set[str] = set()
        for (provider_name, _task), batch in zip(providers, batches, strict=True):
            if isinstance(batch, BaseException):
                error = {
                    "provider": provider_name,
                    "error_type": type(batch).__name__,
                    "error": str(batch).strip()[:240] or "接口未返回错误正文",
                }
                if isinstance(batch, HumanVerificationRequired):
                    error.update(
                        {
                            "verification_required": True,
                            "verification_url": batch.url,
                            "status_code": batch.status_code,
                        }
                    )
                errors.append(error)
                continue
            for item in batch:
                key = item["url"].lower()
                if key not in seen:
                    seen.add(key)
                    results.append(item)
        return results[: max(50, target_sources)], errors

    async def _search_google_scholar(
        self, client: httpx.AsyncClient, query: str
    ) -> list[dict[str, Any]]:
        search_url = self.scholar_search_url(query)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        if self._scholar_cookie_header:
            headers["Cookie"] = self._scholar_cookie_header
        response = await client.get(
            "https://scholar.google.com/scholar",
            params={"hl": "zh-CN", "q": query},
            headers=headers,
        )
        body_lower = response.text.lower()
        if response.status_code in {403, 429, 503} or any(
            marker in body_lower
            for marker in (
                "unusual traffic",
                "not a robot",
                "recaptcha",
                "/sorry/",
                "机器人验证",
                "异常流量",
            )
        ):
            raise HumanVerificationRequired(
                "Google Scholar",
                str(response.url) if response.url else search_url,
                response.status_code,
            )
        response.raise_for_status()
        items = []
        for block in re.findall(
            r'<div class="gs_ri">([\s\S]*?)(?=<div class="gs_ri">|$)',
            response.text,
            flags=re.I,
        )[:10]:
            title_match = re.search(
                r'<h3 class="gs_rt"[^>]*>[\s\S]*?<a[^>]+href="([^"]+)"[^>]*>'
                r"([\s\S]*?)</a>",
                block,
                flags=re.I,
            )
            if not title_match:
                continue
            url = html.unescape(title_match.group(1))
            title = html.unescape(re.sub(r"<[^>]+>", " ", title_match.group(2)))
            title = " ".join(title.split())
            snippet_match = re.search(r'<div class="gs_rs">([\s\S]*?)</div>', block, flags=re.I)
            description = ""
            if snippet_match:
                description = html.unescape(re.sub(r"<[^>]+>", " ", snippet_match.group(1)))
                description = " ".join(description.split())
            author_line = ""
            author_match = re.search(r'<div class="gs_a">([\s\S]*?)</div>', block, flags=re.I)
            if author_match:
                author_line = html.unescape(re.sub(r"<[^>]+>", " ", author_match.group(1)))
                author_line = " ".join(author_line.split())
            year_match = re.search(r"\b(19\d{2}|20\d{2})\b", author_line)
            if not url.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "title": title,
                    "url": url,
                    "scholar_url": self.scholar_url(title),
                    "description": description,
                    "published_year": int(year_match.group(1)) if year_match else None,
                    "source": "Google Scholar",
                    "credibility": self._credibility(
                        source="Google Scholar",
                        url=url,
                        has_abstract=bool(description),
                    ),
                }
            )
        return items

    async def _search_360(self, client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
        response = await client.get(
            "https://www.so.com/s",
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        return self._parse_360_results(response.text)

    def _parse_360_results(self, body: str) -> list[dict[str, Any]]:
        anchors = list(
            re.finditer(
                r'<h3[^>]*class="[^"]*(?:res-title|title)[^"]*"[^>]*>'
                r"[\s\S]*?<a\s+([^>]*)>([\s\S]*?)</a>",
                body,
                flags=re.I,
            )
        )
        items = []
        for anchor in anchors[:12]:
            attributes, raw_title = anchor.group(1), anchor.group(2)
            direct_match = re.search(r'data-mdurl="([^"]+)"', attributes, flags=re.I)
            href_match = re.search(r'href="([^"]+)"', attributes, flags=re.I)
            target = html.unescape(
                (direct_match or href_match).group(1) if (direct_match or href_match) else ""
            )
            title = html.unescape(re.sub(r"<[^>]+>", " ", raw_title))
            title = " ".join(title.split()) or "未命名网页"
            tail = body[anchor.end() : anchor.end() + 2400]
            snippet_match = re.search(
                r'<p[^>]*class="[^"]*(?:res-desc|res-rich)[^"]*"[^>]*>'
                r"([\s\S]*?)</p>",
                tail,
                flags=re.I,
            )
            description = ""
            if snippet_match:
                description = html.unescape(re.sub(r"<[^>]+>", " ", snippet_match.group(1)))
                description = " ".join(description.split())
            if not target.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "title": title,
                    "url": target,
                    "description": description or title,
                    "source": "360 Web Search",
                    "credibility": self._credibility(
                        source="360 Web Search",
                        url=target,
                        has_abstract=False,
                    ),
                }
            )
        return items

    async def _search_duckduckgo(
        self, client: httpx.AsyncClient, query: str
    ) -> list[dict[str, Any]]:
        response = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        anchors = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            response.text,
            flags=re.I | re.S,
        )
        items = []
        for raw_url, raw_title in anchors[:12]:
            search_url = html.unescape(raw_url)
            if search_url.startswith("//"):
                search_url = f"https:{search_url}"
            parsed = urlparse(search_url)
            target = (parse_qs(parsed.query).get("uddg") or [search_url])[0]
            title = html.unescape(re.sub(r"<[^>]+>", " ", raw_title))
            title = " ".join(title.split()) or "未命名网页"
            if not target.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "title": title,
                    "url": target,
                    "description": "",
                    "source": "DuckDuckGo Web Search",
                    "credibility": self._credibility(
                        source="DuckDuckGo Web Search",
                        url=target,
                        has_abstract=False,
                    ),
                }
            )
        return items

    async def _search_bing(self, client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
        response = await client.get(f"https://www.bing.com/search?format=rss&q={quote_plus(query)}")
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        items = []
        for node in root.findall("./channel/item")[:10]:
            title = node.findtext("title") or "未命名网页"
            url = node.findtext("link") or ""
            if url.startswith(("http://", "https://")):
                items.append(
                    {
                        "title": html.unescape(title),
                        "url": url,
                        "description": html.unescape(node.findtext("description") or ""),
                        "source": "Bing Web Search",
                        "credibility": self._credibility(
                            source="Bing Web Search", url=url, has_abstract=False
                        ),
                    }
                )
        return items

    async def _search_crossref(
        self,
        client: httpx.AsyncClient,
        query: str,
        rows: int = 12,
        *,
        year_range: tuple[int, int] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "query.bibliographic": query,
            "rows": max(5, min(int(rows), 80)),
            "sort": "relevance",
            "select": (
                "DOI,title,URL,abstract,published,type,publisher,container-title,"
                "is-referenced-by-count,author,ISSN"
            ),
        }
        if year_range:
            params["filter"] = (
                f"from-pub-date:{year_range[0]}-01-01,until-pub-date:{year_range[1]}-12-31"
            )
        response: httpx.Response | None = None
        for attempt in range(3):
            response = await client.get(
                "https://api.crossref.org/works",
                params=params,
                headers={
                    "User-Agent": ("EvoAgent/1.0.0 (+https://github.com/ElectronicRain/EvoAgent)")
                },
            )
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                break
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after)
            except ValueError:
                delay = float(2**attempt)
            await asyncio.sleep(max(0.5, min(delay, 5.0)))
        assert response is not None
        response.raise_for_status()
        items = []
        for entry in response.json().get("message", {}).get("items", []):
            titles = entry.get("title") or []
            url = entry.get("URL") or (
                f"https://doi.org/{entry['DOI']}" if entry.get("DOI") else ""
            )
            if not url:
                continue
            abstract = re.sub(r"<[^>]+>", " ", entry.get("abstract") or "")
            title = html.unescape(titles[0] if titles else entry.get("DOI", "学术文献"))
            source_type = str(entry.get("type") or "")
            citation_count = int(entry.get("is-referenced-by-count") or 0)
            date_parts = (entry.get("published") or {}).get("date-parts") or []
            published_year = (
                int(date_parts[0][0]) if date_parts and date_parts[0] and date_parts[0][0] else None
            )
            authors = []
            for author in entry.get("author") or []:
                name = " ".join(
                    part for part in [author.get("given"), author.get("family")] if part
                )
                if name:
                    authors.append(name)
            items.append(
                {
                    "title": title,
                    "url": url,
                    "scholar_url": self.scholar_url(title),
                    "description": " ".join(html.unescape(abstract).split()),
                    "source": "Crossref",
                    "doi": entry.get("DOI"),
                    "source_type": source_type,
                    "publisher": entry.get("publisher"),
                    "venue": (entry.get("container-title") or [None])[0],
                    "published_year": published_year,
                    "authors": authors[:12],
                    "citation_count": citation_count,
                    "credibility": self._credibility(
                        source="Crossref",
                        url=url,
                        has_doi=bool(entry.get("DOI")),
                        source_type=source_type,
                        citation_count=citation_count,
                        has_abstract=bool(abstract.strip()),
                    ),
                }
            )
        return items

    def scholar_url(self, title_or_query: str) -> str:
        value = " ".join(str(title_or_query).split())
        quoted = f'"{value}"'
        return f"https://scholar.google.com/scholar?hl=zh-CN&q={quote_plus(quoted)}"

    def scholar_search_url(self, query: str) -> str:
        value = " ".join(str(query).split())
        return f"https://scholar.google.com/scholar?hl=zh-CN&q={quote_plus(value)}"

    def web_search_url(self, query: str) -> str:
        return f"https://www.so.com/s?q={quote_plus(query)}"

    def _credibility(
        self,
        *,
        source: str,
        url: str,
        has_doi: bool = False,
        source_type: str = "",
        citation_count: int = 0,
        has_abstract: bool = False,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        if source == "Crossref":
            score = 62
            reasons.append("Crossref 登记元数据")
        elif source == "Google Scholar":
            score = 60
            reasons.append("Google Scholar 学术检索结果")
        else:
            score = 35
            reasons.append("通用网页检索结果")
        if has_doi:
            score += 10
            reasons.append("具有 DOI")
        if source_type == "journal-article":
            score += 8
            reasons.append("期刊论文类型")
        elif source_type in {"proceedings-article", "book-chapter"}:
            score += 5
            reasons.append("会议论文或学术章节")
        if citation_count:
            citation_bonus = min(8, max(1, len(str(citation_count)) * 2))
            score += citation_bonus
            reasons.append(f"Crossref 被引元数据 {citation_count}")
        if has_abstract:
            score += 3
            reasons.append("包含摘要元数据")
        hostname = (urlparse(url).hostname or "").lower()
        if hostname.endswith((".edu", ".edu.cn", ".gov", ".gov.cn")):
            score += 35
            reasons.append("高校或政府官方域名")
        score = min(96, score)
        if score >= 85:
            level = "高"
        elif score >= 70:
            level = "较高"
        elif score >= 50:
            level = "中"
        else:
            level = "待核验"
        return {
            "score": score,
            "level": level,
            "reasons": reasons,
            "note": "衡量来源与元数据可追溯性，不代表论文结论必然正确",
        }

    def _is_public_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            return False
        try:
            address = ipaddress.ip_address(parsed.hostname)
            return not (address.is_private or address.is_loopback or address.is_link_local)
        except ValueError:
            return True

    async def _fetch_page(self, client: httpx.AsyncClient, url: str) -> tuple[str, str]:
        if not self._is_public_url(url):
            return "", "blocked"
        try:
            response = await client.get(
                url,
                timeout=25,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "gzip, deflate",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    ),
                },
            )
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type and "text" not in content_type:
                return "", "metadata-only"
            extractor = TextExtractor()
            extractor.feed(response.text[:1_000_000])
            return extractor.text()[:12_000], "fetched"
        except Exception as exc:
            return "", f"failed: {str(exc)[:120]}"

    def context(
        self,
        sources: list[dict[str, Any]],
        *,
        char_limit: int = 32_000,
    ) -> str:
        """Build bounded evidence context while retaining every selected citation."""

        char_limit = max(2_000, int(char_limit))
        metadata_blocks: list[str] = []
        contents: list[str] = []
        for index, item in enumerate(sources, 1):
            content = str(item.get("content") or item.get("description") or "仅取得题录信息")
            scholar_line = (
                f"Google Scholar: {item['scholar_url']}\n" if item.get("scholar_url") else ""
            )
            authors = ", ".join(str(value) for value in (item.get("authors") or [])[:8])
            bibliographic_lines = ""
            if authors:
                bibliographic_lines += f"作者: {authors}\n"
            if item.get("published_year"):
                bibliographic_lines += f"年份: {item['published_year']}\n"
            if item.get("venue"):
                bibliographic_lines += f"期刊/会议: {item['venue']}\n"
            if item.get("doi"):
                bibliographic_lines += f"DOI: {item['doi']}\n"
            metadata_blocks.append(
                f"[{index}] {str(item['title'])[:240]}\nURL: {str(item['url'])[:360]}\n"
                f"{scholar_line}"
                f"{bibliographic_lines}"
                f"来源: {item.get('source', 'Web')}\n"
                f"可信度: {(item.get('credibility') or {}).get('level', '待核验')} "
                f"{(item.get('credibility') or {}).get('score', 0)}/100\n"
                f"可用层级: {item.get('status', 'metadata-only')}"
            )
            contents.append(content)
        header = "【网络研究资料】\n"
        fixed_chars = len(header) + sum(len(item) + len("\n内容: \n\n") for item in metadata_blocks)
        remaining = max(0, char_limit - fixed_chars)
        excerpt_limit = max(0, min(900, remaining // max(1, len(metadata_blocks))))
        blocks = [
            f"{metadata}\n内容: {content[:excerpt_limit]}"
            for metadata, content in zip(metadata_blocks, contents, strict=True)
        ]
        result = header + "\n\n".join(blocks)
        return result[:char_limit]


web_research_service = WebResearchService()
