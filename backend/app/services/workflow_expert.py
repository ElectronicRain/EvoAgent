from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AgentDefinition, Extension, KnowledgeBase, ModelEndpoint, Skill
from ..schemas import AgentGenerationConfig, AgentRAGConfig
from .common import dumps
from .llm import provider_from_endpoint
from .tools import tool_runtime
from .workflows import WorkflowEngine


SUPPORTED_NODE_TYPES = {
    "input",
    "agent",
    "knowledge",
    "tool",
    "condition",
    "variable",
    "template",
    "function",
    "merge",
    "artifact",
    "output",
}


class WorkflowExpert:
    async def _full_agent_capabilities(self, db: AsyncSession) -> dict[str, Any]:
        skill_ids = list(
            await db.scalars(
                select(Skill.id).where(Skill.enabled.is_(True)).order_by(Skill.name)
            )
        )
        mcp_extension_ids = list(
            await db.scalars(
                select(Extension.id)
                .where(Extension.enabled.is_(True), Extension.kind == "mcp")
                .order_by(Extension.name)
            )
        )
        image_endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "image",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
        tools = [
            str(item.get("function", {}).get("name") or "")
            for item in tool_runtime.schemas()
        ]
        tools.extend(["exec", "call_agent", "web_research"])
        return {
            "tools": list(dict.fromkeys(item for item in tools if item)),
            "skills": skill_ids,
            "mcp_extensions": mcp_extension_ids,
            "image_model_endpoint_id": image_endpoint.id if image_endpoint else None,
        }

    @staticmethod
    def _resource_text(item: Any) -> str:
        return f"{item.name} {getattr(item, 'description', '')}".lower()

    @staticmethod
    def _position_nodes(nodes: list[dict[str, Any]]) -> None:
        for index, node in enumerate(nodes):
            if node.get("position"):
                continue
            node["position"] = {
                "x": 60 + (index % 5) * 235,
                "y": 120 + (index // 5) * 180,
            }

    @staticmethod
    def _safe_key(value: str, fallback: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", value.strip()).strip("_").lower()
        return normalized[:70] or fallback

    @staticmethod
    def _unique_node_id(definition: dict[str, Any], prefix: str) -> str:
        existing = {str(item.get("id")) for item in definition.get("nodes", [])}
        candidate = prefix
        suffix = 2
        while candidate in existing:
            candidate = f"{prefix}_{suffix}"
            suffix += 1
        return candidate

    def _sanitize_agent_drafts(
        self,
        raw_drafts: list[dict[str, Any]],
        *,
        endpoint: ModelEndpoint | None,
        knowledge_bases: list[KnowledgeBase],
        full_capabilities: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        full_capabilities = full_capabilities or {}
        knowledge_ids = {item.id for item in knowledge_bases}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_drafts[:8], 1):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or f"智能编排 Agent {index}").strip()[:100]
            key = self._safe_key(
                str(raw.get("key") or raw.get("draft_key") or name),
                f"draft_agent_{index}",
            )
            if key in seen:
                continue
            seen.add(key)
            tools = [
                str(item)
                for item in raw.get("tools", [])
                if isinstance(item, str) and item.strip()
            ]
            tools = list(
                dict.fromkeys([*tools, *full_capabilities.get("tools", []), "exec"])
            )
            rag = {
                **AgentRAGConfig().model_dump(),
                **(raw.get("rag_config") if isinstance(raw.get("rag_config"), dict) else {}),
            }
            generation = {
                **AgentGenerationConfig().model_dump(),
                **(
                    raw.get("generation_config")
                    if isinstance(raw.get("generation_config"), dict)
                    else {}
                ),
            }
            try:
                rag = AgentRAGConfig.model_validate(rag).model_dump()
            except ValueError:
                rag = AgentRAGConfig().model_dump()
            try:
                generation = AgentGenerationConfig.model_validate(generation).model_dump()
            except ValueError:
                generation = AgentGenerationConfig().model_dump()
            generation["max_output_tokens"] = max(
                int(generation.get("max_output_tokens") or 0), 8192
            )
            selected_knowledge = [
                str(item)
                for item in raw.get("knowledge_bases", [])
                if str(item) in knowledge_ids
            ]
            result.append(
                {
                    "key": key,
                    "name": name,
                    "description": str(
                        raw.get("description")
                        or f"由工作流智能编排专家为当前任务生成的{name}。"
                    )[:4000],
                    "system_prompt": str(
                        raw.get("system_prompt")
                        or (
                            f"你是{name}。严格围绕工作流传入的任务和上游材料工作，"
                            "输出结构化、可验证、可供下游节点直接使用的结果；"
                            "信息不足时明确说明假设与待补充项。"
                        )
                    ),
                    "provider": endpoint.provider_type if endpoint else "demo",
                    "model_endpoint_id": endpoint.id if endpoint else None,
                    "image_model_endpoint_id": full_capabilities.get(
                        "image_model_endpoint_id"
                    ),
                    "model": endpoint.default_model if endpoint else "demo-model",
                    "temperature": max(
                        0.0, min(float(raw.get("temperature", 0.25)), 2.0)
                    ),
                    "tools": list(dict.fromkeys(tools)),
                    "skills": list(
                        dict.fromkeys(
                            [
                                str(item)
                                for item in raw.get("skills", [])
                                if isinstance(item, str) and item.strip()
                            ]
                            + list(full_capabilities.get("skills", []))
                        )
                    ),
                    "knowledge_bases": selected_knowledge,
                    "rag_config": rag,
                    "generation_config": generation,
                    "permissions": {
                        **(
                            raw.get("permissions")
                            if isinstance(raw.get("permissions"), dict)
                            else {}
                        ),
                        "exec": True,
                        "mcp": True,
                        "skills": True,
                        "security_profile": "workspace-write",
                        "approval_policy": "never",
                        "mcp_extensions": list(
                            full_capabilities.get("mcp_extensions", [])
                        ),
                    },
                }
            )
        return result

    def _sanitize(
        self,
        definition: dict[str, Any],
        agents: list[AgentDefinition],
        knowledge_bases: list[KnowledgeBase],
        agent_draft_keys: set[str] | None = None,
    ) -> dict[str, Any]:
        agent_ids = {item.id for item in agents}
        agent_draft_keys = agent_draft_keys or set()
        knowledge_ids = {item.id for item in knowledge_bases}
        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(definition.get("nodes", [])):
            node = deepcopy(raw)
            node_type = str(node.get("type") or "agent")
            node_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(node.get("id") or f"node_{index}"))
            if node_type not in SUPPORTED_NODE_TYPES or node_id in seen:
                continue
            config = dict(node.get("config") or {})
            if node_type == "agent" and "input" in config:
                config.setdefault("auto_input", False)
            if node_type == "knowledge" and "query" in config:
                config.setdefault("auto_input", False)
            if node_type == "agent":
                real_agent = config.get("agent_id") in agent_ids
                draft_agent = config.get("agent_draft_key") in agent_draft_keys
                if not real_agent and not draft_agent:
                    continue
            if node_type == "knowledge" and config.get("knowledge_base_id") not in knowledge_ids:
                continue
            seen.add(node_id)
            node.update(
                {
                    "id": node_id,
                    "type": node_type,
                    "label": str(node.get("label") or node_type),
                    "config": config,
                }
            )
            nodes.append(node)
        if not any(item["type"] == "input" for item in nodes):
            nodes.insert(0, {"id": "input", "type": "input", "label": "任务输入", "config": {}})
        if not any(item["type"] == "output" for item in nodes):
            nodes.append(
                {
                    "id": "output",
                    "type": "output",
                    "label": "结果输出",
                    "config": {"value": {"result": "{{input.task}}"}},
                }
            )
        ids = {item["id"] for item in nodes}
        edges = []
        edge_keys: set[tuple[str, str, str]] = set()
        for raw in definition.get("edges", []):
            source, target = str(raw.get("source") or ""), str(raw.get("target") or "")
            slot = str(raw.get("source_slot") or raw.get("sourceHandle") or "output")
            key = (source, target, slot)
            if source not in ids or target not in ids or source == target or key in edge_keys:
                continue
            edge_keys.add(key)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "source_slot": slot,
                    "target_slot": str(raw.get("target_slot") or raw.get("targetHandle") or "input"),
                }
            )
        self._position_nodes(nodes)
        sanitized = {
            "nodes": nodes,
            "edges": edges,
            "variables": [
                {
                    "name": str(item.get("name")),
                    "type": str(item.get("type") or "string"),
                    "default": item.get("default", ""),
                    "description": str(item.get("description") or ""),
                    "required": bool(item.get("required", False)),
                }
                for item in definition.get("variables", [])
                if item.get("name")
            ],
            "execution": {
                "loop_enabled": bool(
                    (definition.get("execution") or {}).get("loop_enabled", False)
                ),
                "loop_count": max(
                    1,
                    min(int((definition.get("execution") or {}).get("loop_count", 1)), 50),
                ),
                "artifact_enabled": bool(
                    (definition.get("execution") or {}).get("artifact_enabled", True)
                ),
                "stop_condition": str(
                    (definition.get("execution") or {}).get("stop_condition", "")
                ),
                "intent_validation": bool(
                    (definition.get("execution") or {}).get(
                        "intent_validation",
                        settings.require_online_agents,
                    )
                ),
            },
        }
        WorkflowEngine()._order_nodes(sanitized)
        input_ids = {item["id"] for item in nodes if item["type"] == "input"}
        output_ids = {item["id"] for item in nodes if item["type"] == "output"}
        reachable = set(input_ids)
        changed = True
        while changed:
            changed = False
            for edge in edges:
                if edge["source"] in reachable and edge["target"] not in reachable:
                    reachable.add(edge["target"])
                    changed = True
        if not output_ids.intersection(reachable):
            raise ValueError("编排草案没有从输入到输出的完整可执行路径")
        for node in nodes:
            if node["type"] != "input" and not any(
                edge["target"] == node["id"] for edge in edges
            ):
                raise ValueError(f"节点 {node['label']} 缺少输入连线")
            if node["type"] != "output" and not any(
                edge["source"] == node["id"] for edge in edges
            ):
                raise ValueError(f"节点 {node['label']} 缺少输出连线")
        return sanitized

    def _fallback_definition(
        self,
        message: str,
        current: dict[str, Any] | None,
        agents: list[AgentDefinition],
        knowledge_bases: list[KnowledgeBase],
    ) -> tuple[dict[str, Any], list[str]]:
        lower = message.lower()
        changes: list[str] = []
        if current:
            definition = deepcopy(current)
            definition.setdefault("variables", [])
            definition.setdefault("execution", {})
            count_match = re.search(r"(\d{1,2})\s*(?:次|轮)", message)
            if count_match and any(word in message for word in ("循环", "迭代", "执行")):
                definition["execution"]["loop_enabled"] = int(count_match.group(1)) > 1
                definition["execution"]["loop_count"] = int(count_match.group(1))
                changes.append(f"循环次数调整为 {count_match.group(1)} 轮")
            if any(word in message for word in ("关闭循环", "不要循环", "单次执行")):
                definition["execution"]["loop_enabled"] = False
                definition["execution"]["loop_count"] = 1
                changes.append("切换为单次执行")
            for agent in agents:
                if agent.name in message and any(word in message for word in ("加入", "增加", "添加")):
                    node_id = f"agent_{len(definition.get('nodes', [])) + 1}"
                    definition.setdefault("nodes", []).insert(
                        -1,
                        {
                            "id": node_id,
                            "type": "agent",
                            "label": agent.name,
                            "config": {"agent_id": agent.id, "input": "{{input.task}}"},
                        },
                    )
                    prior = definition["nodes"][-3]["id"] if len(definition["nodes"]) > 2 else "input"
                    definition.setdefault("edges", []).append(
                        {"source": prior, "target": node_id, "source_slot": "output"}
                    )
                    output_node = next(
                        (item for item in definition["nodes"] if item.get("type") == "output"),
                        None,
                    )
                    if output_node:
                        definition["edges"] = [
                            edge
                            for edge in definition["edges"]
                            if edge.get("target") != output_node["id"]
                        ]
                        definition["edges"].append(
                            {"source": node_id, "target": output_node["id"], "source_slot": "output"}
                        )
                        output_node["config"] = {
                            "value": {"result": f"{{{{nodes.{node_id}.output}}}}"}
                        }
                    changes.append(f"加入 Agent：{agent.name}")
            for base in knowledge_bases:
                if base.name in message and any(word in message for word in ("加入", "增加", "添加", "绑定")):
                    node_id = f"knowledge_{len(definition.get('nodes', [])) + 1}"
                    definition.setdefault("nodes", []).insert(
                        1,
                        {
                            "id": node_id,
                            "type": "knowledge",
                            "label": base.name,
                            "config": {
                                "knowledge_base_id": base.id,
                                "query": "{{input.task}}",
                                "top_k": 6,
                            },
                        },
                    )
                    definition.setdefault("edges", []).append(
                        {"source": "input", "target": node_id, "source_slot": "output"}
                    )
                    changes.append(f"加入知识库：{base.name}")
            if changes:
                return definition, changes

        matched_agents = [
            item for item in agents if item.name.lower() in lower or any(
                token in self._resource_text(item)
                for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", lower)
            )
        ]
        if not matched_agents:
            matched_agents = agents[: min(2, len(agents))]
        matched_agents = matched_agents[:3]
        wants_knowledge = any(
            word in message for word in ("知识库", "检索", "资料", "文档", "RAG", "依据")
        )
        matched_knowledge = [
            item for item in knowledge_bases if item.name in message
        ]
        if wants_knowledge and not matched_knowledge:
            matched_knowledge = knowledge_bases[:1]
        matched_knowledge = matched_knowledge[:2]
        nodes: list[dict[str, Any]] = [
            {"id": "input", "type": "input", "label": "任务输入", "config": {}},
            {
                "id": "variables",
                "type": "variable",
                "label": "初始化任务变量",
                "config": {
                    "assignments": [
                        {"name": "objective", "operation": "set", "value": "{{input.task}}"}
                    ]
                },
            },
        ]
        edges: list[dict[str, Any]] = [
            {"source": "input", "target": "variables", "source_slot": "output"}
        ]
        upstream = "variables"
        for index, base in enumerate(matched_knowledge, 1):
            node_id = f"knowledge_{index}"
            nodes.append(
                {
                    "id": node_id,
                    "type": "knowledge",
                    "label": base.name,
                    "config": {
                        "knowledge_base_id": base.id,
                        "query": "{{variables.objective}}",
                        "top_k": 6,
                    },
                }
            )
            edges.append({"source": upstream, "target": node_id, "source_slot": "output"})
            upstream = node_id
            changes.append(f"绑定知识库：{base.name}")
        for index, agent in enumerate(matched_agents, 1):
            node_id = f"agent_{index}"
            input_template = "{{variables.objective}}"
            if upstream.startswith("knowledge"):
                input_template += f"\n\n可追溯资料：\n{{{{nodes.{upstream}.output}}}}"
            elif upstream.startswith("agent"):
                input_template += f"\n\n上游结果：\n{{{{nodes.{upstream}.output}}}}"
            nodes.append(
                {
                    "id": node_id,
                    "type": "agent",
                    "label": agent.name,
                    "config": {"agent_id": agent.id, "input": input_template},
                }
            )
            edges.append({"source": upstream, "target": node_id, "source_slot": "output"})
            upstream = node_id
            changes.append(f"编排 Agent：{agent.name}")
        nodes.extend(
            [
                {
                    "id": "artifact",
                    "type": "artifact",
                    "label": "生成产出文档",
                    "config": {
                        "title": "工作流专业交付文档",
                        "content": (
                            f"# 工作流产出\n\n{{{{nodes.{upstream}.output}}}}"
                            if upstream != "variables"
                            else "# 工作流产出\n\n{{variables.objective}}"
                        ),
                    },
                },
                {
                    "id": "output",
                    "type": "output",
                    "label": "结果输出",
                    "config": {"value": {"result": "{{nodes.artifact.output}}"}},
                },
            ]
        )
        edges.extend(
            [
                {"source": upstream, "target": "artifact", "source_slot": "output"},
                {"source": "artifact", "target": "output", "source_slot": "output"},
            ]
        )
        count_match = re.search(r"(\d{1,2})\s*(?:次|轮)", message)
        loop_count = int(count_match.group(1)) if count_match else 1
        return (
            {
                "nodes": nodes,
                "edges": edges,
                "variables": [
                    {
                        "name": "objective",
                        "type": "string",
                        "default": message,
                        "description": "工作流本轮目标",
                        "required": True,
                    }
                ],
                "execution": {
                    "loop_enabled": loop_count > 1,
                    "loop_count": loop_count,
                    "artifact_enabled": True,
                    "stop_condition": "",
                },
            },
            changes or ["创建输入、变量、产出和输出完整链路"],
        )

    @staticmethod
    def _requested_agent_roles(message: str) -> list[str]:
        if not (
            any(word in message for word in ("创建", "生成", "新增", "新建", "设计"))
            and re.search(r"agent|智能体", message, re.I)
        ):
            return []
        known_roles = [
            "需求分析",
            "质量审核",
            "资料检索",
            "数据分析",
            "内容撰写",
            "风险评估",
            "项目规划",
            "测试验证",
            "事实核验",
            "方案评审",
        ]
        roles = [role for role in known_roles if role in message]
        for match in re.findall(
            r"([\u4e00-\u9fffA-Za-z0-9_-]{2,24})\s*(?:Agent|智能体)",
            message,
            re.I,
        ):
            role = re.sub(
                r"^(?:请|帮我|以及|并且|和|再|创建|生成|新增|新建|设计|一个|一名|新的|新|个)+",
                "",
                match,
            ).strip()
            if role and len(role) <= 16 and role not in {"工作流", "现有", "所有"}:
                roles.append(role)
        unique: list[str] = []
        for role in roles:
            role = role.removesuffix("专家").strip()
            if role and role not in unique:
                unique.append(role)
        return unique[:4] or ["任务执行"]

    def _build_requested_agent_drafts(
        self,
        message: str,
        *,
        endpoint: ModelEndpoint | None,
        knowledge_bases: list[KnowledgeBase],
    ) -> list[dict[str, Any]]:
        drafts: list[dict[str, Any]] = []
        knowledge_ids = [item.id for item in knowledge_bases if item.name in message]
        for index, role in enumerate(self._requested_agent_roles(message), 1):
            role_key_map = {
                "需求分析": "requirements_analyst",
                "质量审核": "quality_reviewer",
                "资料检索": "researcher",
                "数据分析": "data_analyst",
                "内容撰写": "content_writer",
                "风险评估": "risk_assessor",
                "项目规划": "project_planner",
                "测试验证": "test_validator",
                "事实核验": "fact_checker",
                "方案评审": "solution_reviewer",
            }
            key = f"draft_{role_key_map.get(role, self._safe_key(role, f'agent_{index}'))}"
            quality_role = any(word in role for word in ("审核", "核验", "评审", "测试"))
            system_prompt = (
                f"你是工作流中的{role}专家。"
                "接收用户目标、上游节点结果和可用知识材料，先识别输入约束，再完成本岗位任务。"
                "输出必须结构化、明确标注结论与依据，并列出风险、缺口和下一节点可直接使用的字段。"
            )
            if quality_role:
                system_prompt += (
                    "你必须在首行只输出“DECISION: PASS”或“DECISION: REVISE”的明确判定；"
                    "不通过时列出问题、证据和修改建议。"
                )
            drafts.append(
                {
                    "key": key,
                    "name": f"{role} Agent",
                    "description": f"负责工作流中的{role}，由智能编排专家自动生成，可在 Agent 工厂继续编辑。",
                    "system_prompt": system_prompt,
                    "provider": endpoint.provider_type if endpoint else "demo",
                    "model_endpoint_id": endpoint.id if endpoint else None,
                    "model": endpoint.default_model if endpoint else "demo-model",
                    "temperature": 0.15 if quality_role else 0.3,
                    "tools": ["read_file", "search_files", "exec"],
                    "skills": [],
                    "knowledge_bases": knowledge_ids,
                    "rag_config": {
                        **AgentRAGConfig().model_dump(),
                        "enabled": bool(knowledge_ids),
                    },
                    "generation_config": AgentGenerationConfig().model_dump(),
                    "permissions": {
                        "exec": True,
                        "mcp": True,
                        "skills": True,
                        "security_profile": "workspace-write",
                        "approval_policy": "never",
                    },
                }
            )
        return drafts

    def _insert_agent_drafts(
        self,
        definition: dict[str, Any],
        drafts: list[dict[str, Any]],
        changes: list[str],
    ) -> None:
        nodes = definition.setdefault("nodes", [])
        edges = definition.setdefault("edges", [])
        existing_drafts = {
            str((node.get("config") or {}).get("agent_draft_key"))
            for node in nodes
            if node.get("type") == "agent"
        }
        for draft in drafts:
            if draft["key"] in existing_drafts:
                continue
            target = next(
                (node for node in nodes if node.get("type") == "artifact"),
                next((node for node in nodes if node.get("type") == "output"), None),
            )
            if target is None:
                continue
            incoming = next(
                (edge for edge in reversed(edges) if edge.get("target") == target["id"]),
                None,
            )
            if incoming is None:
                continue
            upstream = str(incoming["source"])
            edges.remove(incoming)
            node_id = self._unique_node_id(definition, draft["key"].removeprefix("draft_"))
            upstream_context = (
                "" if upstream == "input" else f"\n\n上游结果：\n{{{{nodes.{upstream}.output}}}}"
            )
            nodes.insert(
                nodes.index(target),
                {
                    "id": node_id,
                    "type": "agent",
                    "label": draft["name"],
                    "config": {
                        "agent_draft_key": draft["key"],
                        "input": f"任务：{{{{input.task}}}}{upstream_context}",
                        "auto_input": False,
                    },
                },
            )
            edges.extend(
                [
                    {"source": upstream, "target": node_id, "source_slot": "output"},
                    {"source": node_id, "target": target["id"], "source_slot": "output"},
                ]
            )
            changes.append(f"生成新 Agent 完整配置：{draft['name']}")

    def _augment_requested_branch(
        self,
        definition: dict[str, Any],
        message: str,
        changes: list[str],
    ) -> None:
        wants_branch = any(
            word in message
            for word in ("新增分支", "新分支", "分支", "支路", "通过/不通过", "否则")
        )
        if not wants_branch:
            return
        nodes = definition.setdefault("nodes", [])
        edges = definition.setdefault("edges", [])
        if any(node.get("type") == "condition" for node in nodes) and not any(
            word in message for word in ("再加", "新增", "增加", "添加")
        ):
            return
        target = next(
            (node for node in nodes if node.get("type") == "artifact"),
            next((node for node in nodes if node.get("type") == "output"), None),
        )
        if target is None:
            return
        incoming = next(
            (edge for edge in reversed(edges) if edge.get("target") == target["id"]),
            None,
        )
        if incoming is None:
            return
        upstream = str(incoming["source"])
        edges.remove(incoming)
        condition_id = self._unique_node_id(definition, "quality_gate")
        true_id = self._unique_node_id(definition, "approved_path")
        false_id = self._unique_node_id(definition, "revision_path")
        merge_id = self._unique_node_id(definition, "branch_merge")
        insert_at = nodes.index(target)
        nodes[insert_at:insert_at] = [
            {
                "id": condition_id,
                "type": "condition",
                "label": "质量判定分支",
                "config": {
                    "left": f"{{{{nodes.{upstream}.output}}}}",
                    "operator": "contains",
                    "right": "DECISION: PASS",
                },
            },
            {
                "id": true_id,
                "type": "template",
                "label": "通过支路",
                "config": {
                    "template": f"## 审核通过\n\n{{{{nodes.{upstream}.output}}}}"
                },
            },
            {
                "id": false_id,
                "type": "template",
                "label": "待修订支路",
                "config": {
                    "template": f"## 审核未通过，进入修订\n\n{{{{nodes.{upstream}.output}}}}"
                },
            },
            {
                "id": merge_id,
                "type": "merge",
                "label": "汇合分支结果",
                "config": {"mode": "text", "separator": "\n\n"},
            },
        ]
        edges.extend(
            [
                {"source": upstream, "target": condition_id, "source_slot": "output"},
                {"source": condition_id, "target": true_id, "source_slot": "true"},
                {"source": condition_id, "target": false_id, "source_slot": "false"},
                {"source": true_id, "target": merge_id, "source_slot": "output"},
                {"source": false_id, "target": merge_id, "source_slot": "output"},
                {"source": merge_id, "target": target["id"], "source_slot": "output"},
            ]
        )
        if target.get("type") == "artifact":
            target.setdefault("config", {})["content"] = (
                f"# 工作流产出\n\n{{{{nodes.{merge_id}.output}}}}"
            )
        changes.append("创建可执行的通过/不通过条件分支、双支路与汇合节点")

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
        candidate = fenced.group(1) if fenced else content[content.find("{") : content.rfind("}") + 1]
        if not candidate:
            return None
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None

    async def chat(
        self,
        db: AsyncSession,
        *,
        message: str,
        history: list[dict[str, str]],
        current_definition: dict[str, Any] | None,
        current_agent_drafts: list[dict[str, Any]] | None,
        workflow_name: str,
        workflow_description: str,
    ) -> dict[str, Any]:
        agents = (
            await db.scalars(
                select(AgentDefinition)
                .where(AgentDefinition.status.in_(["active", "candidate"]))
                .order_by(AgentDefinition.name)
            )
        ).all()
        knowledge_bases = (await db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name))).all()
        fallback, fallback_changes = self._fallback_definition(
            message, current_definition, list(agents), list(knowledge_bases)
        )
        proposed = fallback
        raw_agent_drafts = deepcopy(current_agent_drafts or [])
        reply = ""
        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "chat",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
        if settings.require_online_agents and not endpoint:
            raise ValueError(
                "工作流智能编排需要在线对话模型接口。"
                "请先在“扩展与模型”中配置并启用现有接口。"
            )
        full_capabilities = await self._full_agent_capabilities(db)
        if endpoint:
            resources = {
                "agents": [
                    {"id": item.id, "name": item.name, "description": item.description}
                    for item in agents
                ],
                "knowledge_bases": [
                    {"id": item.id, "name": item.name, "description": item.description}
                    for item in knowledge_bases
                ],
            }
            system = (
                "你是 EvoAgent 工作流智能编排专家。根据用户目标生成或修改可执行 DAG。"
                "现有 Agent 和知识库必须使用资源清单中的真实 ID；如果现有 Agent 不适合，"
                "你必须在 new_agents 中生成新的 Agent 完整配置，并让对应 agent 节点使用"
                " config.agent_draft_key 引用 new_agents.key，不能捏造 agent_id。"
                "新 Agent 配置包含 key、name、description、system_prompt、temperature、"
                "tools、skills、knowledge_bases、rag_config、generation_config、permissions。"
                "节点类型可用：input、agent、knowledge、tool、"
                "condition、variable、template、function、merge、artifact、output。"
                "条件节点使用 true/false source_slot；变量引用使用 {{variables.name}}，"
                "节点引用使用 {{nodes.node_id.output}}。必须保留 input 到 output 的完整路径，"
                "不得创建结构环；用户要求分支时必须建立 condition、至少两条 true/false 支路"
                "以及 merge 汇合节点；重复执行使用 execution.loop_enabled/loop_count。"
                "只返回 JSON："
                '{"reply":"给用户的说明","name":"工作流名","description":"说明",'
                '"change_summary":["修改"],"new_agents":[{"key":"draft_role","name":"角色 Agent",'
                '"description":"职责","system_prompt":"完整系统提示词","temperature":0.2,'
                '"tools":["exec"],"skills":[],"knowledge_bases":[],"rag_config":{},'
                '"generation_config":{},"permissions":{}}],'
                '"definition":{"nodes":[],"edges":[],'
                '"variables":[],"execution":{}}}。'
            )
            user_payload = {
                "message": message,
                "history": history[-12:],
                "workflow": {
                    "name": workflow_name,
                    "description": workflow_description,
                    "definition": current_definition,
                    "agent_drafts": current_agent_drafts or [],
                },
                "resources": resources,
            }
            try:
                response = await provider_from_endpoint(endpoint).chat(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": dumps(user_payload)},
                    ],
                    model=endpoint.default_model,
                    temperature=0.1,
                    max_output_tokens=8000,
                )
                parsed = self._extract_json(response.content)
                if parsed and isinstance(parsed.get("definition"), dict):
                    proposed = parsed["definition"]
                    if isinstance(parsed.get("new_agents"), list):
                        raw_agent_drafts = [
                            *raw_agent_drafts,
                            *[
                                item
                                for item in parsed["new_agents"]
                                if isinstance(item, dict)
                            ],
                        ]
                    fallback_changes = [
                        str(item) for item in parsed.get("change_summary", []) if str(item)
                    ] or fallback_changes
                    reply = str(parsed.get("reply") or "")
                    workflow_name = str(parsed.get("name") or workflow_name)
                    workflow_description = str(
                        parsed.get("description") or workflow_description
                    )
            except Exception:
                pass
        requested_drafts = self._build_requested_agent_drafts(
            message,
            endpoint=endpoint,
            knowledge_bases=list(knowledge_bases),
        )
        existing_draft_keys = {
            self._safe_key(
                str(item.get("key") or item.get("draft_key") or ""),
                f"draft_agent_{index}",
            )
            for index, item in enumerate(raw_agent_drafts, 1)
            if isinstance(item, dict)
        }
        raw_agent_drafts.extend(
            item for item in requested_drafts if item["key"] not in existing_draft_keys
        )
        agent_drafts = self._sanitize_agent_drafts(
            raw_agent_drafts,
            endpoint=endpoint,
            knowledge_bases=list(knowledge_bases),
            full_capabilities=full_capabilities,
        )
        self._insert_agent_drafts(proposed, agent_drafts, fallback_changes)
        self._augment_requested_branch(proposed, message, fallback_changes)
        proposed.setdefault("execution", {})
        explicit_count = re.search(r"(\d{1,2})\s*(?:次|轮)", message)
        if explicit_count and any(word in message for word in ("循环", "迭代", "执行")):
            count = max(1, min(int(explicit_count.group(1)), 50))
            proposed["execution"]["loop_enabled"] = count > 1
            proposed["execution"]["loop_count"] = count
            fallback_changes = [
                item for item in fallback_changes if "循环" not in item
            ]
            fallback_changes.append(f"按明确要求设置为循环 {count} 轮")
        if any(word in message for word in ("每轮生成", "产出文档", "交付文档")):
            proposed["execution"]["artifact_enabled"] = True
        draft_keys = {item["key"] for item in agent_drafts}
        try:
            sanitized = self._sanitize(
                proposed,
                list(agents),
                list(knowledge_bases),
                draft_keys,
            )
        except (TypeError, ValueError):
            safe_fallback, fallback_changes = self._fallback_definition(
                message, None, list(agents), list(knowledge_bases)
            )
            self._insert_agent_drafts(safe_fallback, agent_drafts, fallback_changes)
            self._augment_requested_branch(
                safe_fallback,
                message,
                fallback_changes,
            )
            sanitized = self._sanitize(
                safe_fallback,
                list(agents),
                list(knowledge_bases),
                draft_keys,
            )
        return {
            "reply": reply
            or (
                "我已把你的描述转换为可手动编辑的专业工作流草案。"
                "节点、分支、变量、知识库、循环和产出配置都可以继续调整；"
                "你也可以继续告诉我增删哪些节点或修改哪项参数。"
            ),
            "name": workflow_name.strip() or "智能编排工作流",
            "description": workflow_description.strip() or message[:240],
            "change_summary": fallback_changes,
            "definition": sanitized,
            "agent_drafts": agent_drafts,
            "resource_snapshot": {
                "agent_count": len(agents),
                "knowledge_base_count": len(knowledge_bases),
                "model_endpoint": endpoint.name if endpoint else "本地规则专家",
            },
        }

    async def _unique_slug(self, db: AsyncSession, value: str) -> str:
        base = self._safe_key(value, "workflow-agent").replace("_", "-")[:86]
        candidate = base
        suffix = 2
        while await db.scalar(
            select(AgentDefinition.id).where(AgentDefinition.slug == candidate)
        ):
            candidate = f"{base[:92 - len(str(suffix))]}-{suffix}"
            suffix += 1
        return candidate

    async def materialize(
        self,
        db: AsyncSession,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        definition = deepcopy(proposal.get("definition") or {})
        endpoint = await db.scalar(
            select(ModelEndpoint)
            .where(
                ModelEndpoint.enabled.is_(True),
                ModelEndpoint.modality == "chat",
            )
            .order_by(desc(ModelEndpoint.updated_at))
        )
        if settings.require_online_agents and not endpoint:
            raise ValueError(
                "创建工作流 Agent 需要在线对话模型接口，请先启用现有接口"
            )
        full_capabilities = await self._full_agent_capabilities(db)
        knowledge_bases = (
            await db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.name))
        ).all()
        drafts = self._sanitize_agent_drafts(
            [
                item
                for item in proposal.get("agent_drafts", [])
                if isinstance(item, dict)
            ],
            endpoint=endpoint,
            knowledge_bases=list(knowledge_bases),
            full_capabilities=full_capabilities,
        )
        referenced_keys = {
            str((node.get("config") or {}).get("agent_draft_key"))
            for node in definition.get("nodes", [])
            if node.get("type") == "agent"
            and (node.get("config") or {}).get("agent_draft_key")
        }
        draft_map = {item["key"]: item for item in drafts}
        missing = sorted(referenced_keys.difference(draft_map))
        if missing:
            raise ValueError(f"工作流引用了缺少配置的新 Agent：{', '.join(missing)}")

        created: list[AgentDefinition] = []
        created_by_key: dict[str, AgentDefinition] = {}
        for key in sorted(referenced_keys):
            draft = draft_map[key]
            slug = await self._unique_slug(db, key.removeprefix("draft_"))
            item = AgentDefinition(
                name=draft["name"],
                slug=slug,
                description=draft["description"],
                system_prompt=draft["system_prompt"],
                provider=draft["provider"],
                model_endpoint_id=draft["model_endpoint_id"],
                image_model_endpoint_id=draft.get("image_model_endpoint_id"),
                model=draft["model"],
                temperature=draft["temperature"],
                tools_json=dumps(draft["tools"]),
                skills_json=dumps(draft["skills"]),
                knowledge_bases_json=dumps(draft["knowledge_bases"]),
                rag_config_json=dumps(draft["rag_config"]),
                generation_config_json=dumps(draft["generation_config"]),
                permissions_json=dumps(draft["permissions"]),
                status="candidate",
                is_template=False,
            )
            db.add(item)
            await db.flush()
            created.append(item)
            created_by_key[key] = item

        for node in definition.get("nodes", []):
            config = node.get("config") or {}
            draft_key = config.pop("agent_draft_key", None)
            if draft_key:
                config["agent_id"] = created_by_key[str(draft_key)].id
                if "input" in config:
                    config["auto_input"] = False
                node["config"] = config

        agents = (await db.scalars(select(AgentDefinition))).all()
        sanitized = self._sanitize(
            definition,
            list(agents),
            list(knowledge_bases),
        )
        result = deepcopy(proposal)
        result["definition"] = sanitized
        result["agent_drafts"] = []
        result["created_agents"] = [
            {
                "id": item.id,
                "name": item.name,
                "slug": item.slug,
                "description": item.description,
                "system_prompt": item.system_prompt,
                "provider": item.provider,
                "model_endpoint_id": item.model_endpoint_id,
                "model": item.model,
                "temperature": item.temperature,
                "tools_json": item.tools_json,
                "skills_json": item.skills_json,
                "knowledge_bases_json": item.knowledge_bases_json,
                "rag_config_json": item.rag_config_json,
                "generation_config_json": item.generation_config_json,
                "permissions_json": item.permissions_json,
                "status": item.status,
            }
            for item in created
        ]
        return result


workflow_expert = WorkflowExpert()
