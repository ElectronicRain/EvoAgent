from __future__ import annotations

import asyncio
import html
import ipaddress
import re
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote_plus, urlparse
from xml.etree import ElementTree

import httpx


EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


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
    trigger = re.compile(
        r"综述|文献|资料|检索|搜索|查找|调查|调研|查询|了解|联网|网络|网页|"
        r"review|survey|search|research",
        re.I,
    )
    academic_trigger = re.compile(
        r"学术|文献|论文|综述|期刊|会议论文|参考文献|引用|引文|DOI|"
        r"研究现状|研究进展|systematic review|literature review|paper|citation",
        re.I,
    )

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
        return bool(self.trigger.search(task))

    def research_mode(self, task: str) -> str:
        return "academic" if self.academic_trigger.search(task) else "web"

    def requested_source_count(self, task: str) -> int:
        """Return a bounded, user-requested evidence count for research tasks."""
        patterns = (
            r"(?:至少|不少于|约|大约|检索|搜寻|纳入|包含|覆盖)?\s*(\d{1,3})\s*(?:篇|条)\s*(?:文献|论文|资料)?",
            r"(\d{1,3})\s*(?:papers?|articles?|references?|studies)",
        )
        for pattern in patterns:
            match = re.search(pattern, task, re.I)
            if match:
                return max(5, min(int(match.group(1)), 80))
        return 12

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

    def query_variants(self, task: str) -> list[str]:
        mode = self.research_mode(task)
        institution = self.institution_name(task)
        if institution and mode == "web":
            return [
                f'"{institution}" 官网 招生',
                f'"{institution}" 师资 学科专业',
                f'"{institution}" 就业质量报告 录取分数',
                f'"{institution}" 校园生活 宿舍',
            ]

        cleaned = re.sub(
            r"帮我|请|关于|完成|写一份|总结|综述|调查|调研|查询|查找",
            " ",
            task,
            flags=re.I,
        )
        cleaned = cleaned.replace("的", " ")
        cleaned = " ".join(cleaned.split()).strip(" ：:，,。")
        translations = {
            "二维": "2D",
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
        values = []
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
        base_query = cleaned or task
        values.append(base_query)
        if len(values) == 1:
            if mode == "academic":
                values.extend(
                    [
                        f"{base_query} literature review",
                        f"{base_query} methods evaluation",
                        f"{base_query} latest research",
                    ]
                )
            else:
                values.extend(
                    [
                        f"{base_query} 官方 权威来源",
                        f"{base_query} 最新 数据 报告",
                        f"{base_query} 背景 现状",
                    ]
                )
        return list(dict.fromkeys(values))[:4]

    async def collect(self, task: str, on_event: EventHandler) -> list[dict[str, Any]]:
        mode = self.research_mode(task)
        queries = self.query_variants(task)
        target_sources = self.requested_source_count(task) if mode == "academic" else 12
        await on_event(
            {
                "type": "research_planning",
                "queries": queries,
                "strategy": "comprehensive",
                "mode": mode,
                "target_sources": target_sources,
            }
        )
        candidates: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "EvoAgent/0.1 academic-research"},
        ) as client:
            for query in queries:
                search_url = (
                    self.scholar_url(query) if mode == "academic" else self.web_search_url(query)
                )
                await on_event(
                    {
                        "type": "web_search_started",
                        "query": query,
                        "mode": mode,
                        "search_url": search_url,
                        "search_label": (
                            "Google Scholar 学术检索"
                            if mode == "academic"
                            else "普通网页检索"
                        ),
                        "scholar_url": search_url if mode == "academic" else None,
                    }
                )
                results = await self._search(client, query, mode, target_sources)
                relevant = self._rank_results(task, results)
                await on_event(
                    {
                        "type": "web_search_results",
                        "query": query,
                        "mode": mode,
                        "count": len(relevant),
                        "discarded": len(results) - len(relevant),
                        "results": [
                            {
                                "title": item["title"],
                                "url": item["url"],
                                "scholar_url": item.get("scholar_url"),
                                "relevance": item.get("relevance", 0),
                                "credibility": item.get("credibility"),
                            }
                            for item in relevant[:10]
                        ],
                    }
                )
                candidates.extend(relevant)

            sources = self._rank_results(task, candidates)[:target_sources]
            await on_event(
                {
                    "type": "research_sources_selected",
                    "count": len(sources),
                    "results": [
                        {
                            "title": item["title"],
                            "url": item["url"],
                            "source": item.get("source", "Web"),
                            "mode": mode,
                            "scholar_url": item.get("scholar_url"),
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

    def _rank_results(
        self, task: str, results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        task_lower = task.lower()
        groups: list[tuple[str, list[str], int, bool]] = []
        institution = self.institution_name(task)
        if institution:
            groups.append(("institution", [institution.lower()], 8, True))
        if re.search(r"(?<![a-z0-9])2d(?![a-z0-9])|二维", task_lower):
            groups.append(("dimension", ["2d", "two-dimensional", "二维"], 4, True))
        if re.search(r"结构化网格|structured\s+(grid|mesh)", task_lower):
            groups.append(
                (
                    "structured_mesh",
                    ["structured mesh", "structured grid", "block structured", "结构化网格"],
                    5,
                    True,
                )
            )
        if re.search(r"网格|\bmesh\b|\bgrid\b", task_lower):
            groups.append(("mesh", ["mesh", "grid", "网格"], 4, True))
        if re.search(r"质量|评估|quality|assessment|evaluation", task_lower):
            groups.append(
                (
                    "quality",
                    ["quality", "assessment", "evaluation", "metric", "质量", "评估"],
                    3,
                    False,
                )
            )

        ranked: dict[str, dict[str, Any]] = {}
        for item in results:
            text = html.unescape(
                f"{item.get('title', '')} {item.get('description', '')}"
            ).lower()
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
            if any(name == "quality" for name, *_rest in groups) and "quality" not in matched:
                # Background work on structured mesh generation is useful, but ranks
                # below papers that explicitly discuss metrics/evaluation.
                score -= 2
            hostname = (urlparse(str(item.get("url", ""))).hostname or "").lower()
            if institution and hostname.endswith((".edu.cn", ".edu", ".gov.cn", ".gov")):
                score += 6
            if not groups:
                tokens = {
                    token
                    for token in re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", task_lower)
                    if token not in {"review", "survey"}
                }
                overlap = sum(token in text for token in tokens)
                if not overlap:
                    continue
                score += overlap
            enriched = {**item, "relevance": score, "matched_concepts": matched}
            key = str(item.get("doi") or item.get("url", "")).lower()
            current = ranked.get(key)
            if current is None or score > current.get("relevance", 0):
                ranked[key] = enriched
        return sorted(ranked.values(), key=lambda item: item.get("relevance", 0), reverse=True)

    async def _search(
        self, client: httpx.AsyncClient, query: str, mode: str, target_sources: int = 12
    ) -> list[dict[str, Any]]:
        if mode == "academic":
            tasks = [
                self._search_google_scholar(client, query),
                self._search_crossref(client, query, rows=max(12, target_sources)),
            ]
        else:
            tasks = [
                self._search_360(client, query),
                self._search_duckduckgo(client, query),
                self._search_bing(client, query),
            ]
        # Individual provider failures do not suppress the remaining providers.
        batches = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for batch in batches:
            if isinstance(batch, BaseException):
                continue
            for item in batch:
                key = item["url"].lower()
                if key not in seen:
                    seen.add(key)
                    results.append(item)
        return results[:50]

    async def _search_google_scholar(
        self, client: httpx.AsyncClient, query: str
    ) -> list[dict[str, Any]]:
        response = await client.get(
            "https://scholar.google.com/scholar",
            params={"hl": "zh-CN", "q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
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
            snippet_match = re.search(
                r'<div class="gs_rs">([\s\S]*?)</div>', block, flags=re.I
            )
            description = ""
            if snippet_match:
                description = html.unescape(re.sub(r"<[^>]+>", " ", snippet_match.group(1)))
                description = " ".join(description.split())
            if not url.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "title": title,
                    "url": url,
                    "scholar_url": self.scholar_url(title),
                    "description": description,
                    "source": "Google Scholar",
                    "credibility": self._credibility(
                        source="Google Scholar",
                        url=url,
                        has_abstract=bool(description),
                    ),
                }
            )
        return items

    async def _search_360(
        self, client: httpx.AsyncClient, query: str
    ) -> list[dict[str, Any]]:
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
        anchors = list(re.finditer(
            r'<h3[^>]*class="[^"]*(?:res-title|title)[^"]*"[^>]*>'
            r"[\s\S]*?<a\s+([^>]*)>([\s\S]*?)</a>",
            body,
            flags=re.I,
        ))
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
                description = html.unescape(
                    re.sub(r"<[^>]+>", " ", snippet_match.group(1))
                )
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
        self, client: httpx.AsyncClient, query: str, rows: int = 12
    ) -> list[dict[str, Any]]:
        response = await client.get(
            "https://api.crossref.org/works",
            params={
                "query.bibliographic": query,
                "rows": max(5, min(int(rows), 80)),
                "sort": "relevance",
                "select": (
                    "DOI,title,URL,abstract,published,type,publisher,container-title,"
                    "is-referenced-by-count,author,ISSN"
                ),
            },
        )
        response.raise_for_status()
        items = []
        for entry in response.json().get("message", {}).get("items", []):
            titles = entry.get("title") or []
            url = entry.get("URL") or (f"https://doi.org/{entry['DOI']}" if entry.get("DOI") else "")
            if not url:
                continue
            abstract = re.sub(r"<[^>]+>", " ", entry.get("abstract") or "")
            title = html.unescape(titles[0] if titles else entry.get("DOI", "学术文献"))
            source_type = str(entry.get("type") or "")
            citation_count = int(entry.get("is-referenced-by-count") or 0)
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
            content = str(
                item.get("content") or item.get("description") or "仅取得题录信息"
            )
            scholar_line = (
                f"Google Scholar: {item['scholar_url']}\n"
                if item.get("scholar_url")
                else ""
            )
            metadata_blocks.append(
                f"[{index}] {str(item['title'])[:240]}\nURL: {str(item['url'])[:360]}\n"
                f"{scholar_line}"
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
