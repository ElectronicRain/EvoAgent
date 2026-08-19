from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    AgentConversation,
    AgentDefinition,
    AgentMessage,
    AgentRun,
    UserAccount,
    UserPreference,
    UserQuestionMemory,
    UserSession,
)
from .common import dumps, loads
from .intent import intent_service


REPLY_STYLES: list[dict[str, str]] = [
    {
        "id": "balanced",
        "name": "自然均衡",
        "description": "清晰、自然，在信息量与阅读负担之间保持平衡。",
        "prompt": "以自然、清晰、均衡的方式回复；先给结论，再补充必要依据。",
    },
    {
        "id": "concise",
        "name": "简洁直接",
        "description": "减少铺垫，优先给出结论、步骤和关键数字。",
        "prompt": "回复要简洁直接，省略寒暄和重复解释；优先使用短段落给出结论与行动项。",
    },
    {
        "id": "professional",
        "name": "专业顾问",
        "description": "结构严谨、术语准确，明确风险与执行建议。",
        "prompt": "以专业顾问风格回复，术语准确、结构严谨，并明确说明风险、依据和下一步建议。",
    },
    {
        "id": "friendly",
        "name": "亲切伙伴",
        "description": "语气温和、有耐心，适合连续讨论和共同探索。",
        "prompt": "以亲切、耐心、有同理心的协作伙伴语气回复，避免生硬表达，但保持信息准确。",
    },
    {
        "id": "academic",
        "name": "学术严谨",
        "description": "强调定义、证据、引用和事实与推断的边界。",
        "prompt": "以学术严谨风格回复：定义清楚、论证连贯、标注来源，并区分事实、推断和不确定性。",
    },
    {
        "id": "creative",
        "name": "创意启发",
        "description": "主动提供新视角、类比和备选思路。",
        "prompt": "以富有创意和启发性的方式回复，主动给出新视角、恰当类比与多个可行方案。",
    },
    {
        "id": "teacher",
        "name": "耐心教师",
        "description": "由浅入深讲解，使用例子并检查关键理解点。",
        "prompt": "像耐心教师一样由浅入深讲解，拆分复杂概念，提供例子，并突出容易混淆的关键点。",
    },
    {
        "id": "detailed",
        "name": "深度详尽",
        "description": "提供完整背景、过程、边界条件和替代方案。",
        "prompt": "提供深入、完整的回复，覆盖背景、推理依据、执行过程、边界条件和替代方案。",
    },
    {
        "id": "custom",
        "name": "自定义风格",
        "description": "使用你自己编写的全局回复要求。",
        "prompt": "",
    },
]
STYLE_MAP = {item["id"]: item for item in REPLY_STYLES}
LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _password_hash(password: str, *, iterations: int = 240_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _password_valid(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class UserService:
    async def preference(self, db: AsyncSession, user_id: str) -> UserPreference:
        item = await db.get(UserPreference, user_id)
        if item is None:
            item = UserPreference(user_id=user_id)
            db.add(item)
            await db.flush()
        return item

    def public_user(
        self, user: UserAccount, preference: UserPreference | None = None
    ) -> dict[str, Any]:
        result = {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "avatar_color": user.avatar_color,
            "role": user.role,
            "status": user.status,
            "created_at": user.created_at,
            "last_login_at": user.last_login_at,
            "last_active_at": user.last_active_at,
        }
        if preference is not None:
            result.update(
                {
                    "reply_style_id": preference.reply_style_id,
                    "custom_reply_style": preference.custom_reply_style,
                    "memory_enabled": preference.memory_enabled,
                }
            )
        return result

    async def issue_session(
        self, db: AsyncSession, user: UserAccount
    ) -> tuple[str, UserPreference]:
        token = secrets.token_urlsafe(36)
        now = datetime.now(timezone.utc)
        db.add(
            UserSession(
                user_id=user.id,
                token_hash=_token_hash(token),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(days=30),
            )
        )
        user.last_login_at = now
        user.last_active_at = now
        preference = await self.preference(db, user.id)
        await db.flush()
        return token, preference

    async def register(
        self,
        db: AsyncSession,
        *,
        username: str,
        display_name: str,
        password: str,
    ) -> dict[str, Any]:
        normalized = username.strip()
        existing = await db.scalar(
            select(UserAccount).where(func.lower(UserAccount.username) == normalized.lower())
        )
        if existing:
            raise ValueError("用户名已存在")
        first_user = (await db.scalar(select(func.count(UserAccount.id))) or 0) == 0
        user = UserAccount(
            username=normalized,
            display_name=display_name.strip(),
            password_hash=_password_hash(password),
        )
        db.add(user)
        await db.flush()
        preference = await self.preference(db, user.id)
        if first_user:
            await db.execute(
                update(AgentConversation)
                .where(AgentConversation.user_id.is_(None))
                .values(user_id=user.id)
            )
            await db.execute(
                update(AgentRun)
                .where(AgentRun.user_id.is_(None))
                .values(user_id=user.id)
            )
            rows = (
                await db.execute(
                    select(AgentMessage, AgentConversation).join(
                        AgentConversation,
                        AgentConversation.id == AgentMessage.conversation_id,
                    ).where(AgentMessage.role == "user")
                )
            ).all()
            for message, conversation in rows:
                db.add(
                    self._memory(
                        user.id,
                        conversation.id,
                        conversation.agent_id,
                        message.content,
                        message.created_at,
                    )
                )
        token, preference = await self.issue_session(db, user)
        return {
            "token": token,
            "user": self.public_user(user, preference),
            "claimed_legacy_data": first_user,
        }

    async def login(
        self, db: AsyncSession, *, username: str, password: str
    ) -> dict[str, Any]:
        user = await db.scalar(
            select(UserAccount).where(
                func.lower(UserAccount.username) == username.strip().lower()
            )
        )
        if user is None or not _password_valid(password, user.password_hash):
            raise ValueError("用户名或密码不正确")
        if user.status != "active":
            raise ValueError("账号已停用")
        token, preference = await self.issue_session(db, user)
        return {"token": token, "user": self.public_user(user, preference)}

    async def resolve(
        self, db: AsyncSession, authorization: str | None
    ) -> UserAccount | None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        token = authorization.split(" ", 1)[1].strip()
        if not token:
            return None
        session = await db.scalar(
            select(UserSession).where(UserSession.token_hash == _token_hash(token))
        )
        if session is None:
            return None
        now = datetime.now(timezone.utc)
        if _aware(session.expires_at) <= now:
            await db.delete(session)
            return None
        user = await db.get(UserAccount, session.user_id)
        if user is None or user.status != "active":
            return None
        session.last_seen_at = now
        if user.last_active_at is None or (now - _aware(user.last_active_at)).total_seconds() >= 60:
            user.last_active_at = now
        return user

    async def logout(self, db: AsyncSession, authorization: str | None) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            return
        token = authorization.split(" ", 1)[1].strip()
        session = await db.scalar(
            select(UserSession).where(UserSession.token_hash == _token_hash(token))
        )
        if session is not None:
            await db.delete(session)

    def reply_style_prompt(self, preference: UserPreference) -> str:
        if preference.reply_style_id == "custom":
            custom = preference.custom_reply_style.strip()
            return (
                "【用户全局回复风格】\n"
                + (custom or STYLE_MAP["balanced"]["prompt"])
                + "\n此风格适用于本轮所有 Agent，但不得覆盖安全、事实准确性和引用要求。"
            )
        selected = STYLE_MAP.get(preference.reply_style_id, STYLE_MAP["balanced"])
        return (
            "【用户全局回复风格】\n"
            f"{selected['prompt']}\n"
            "此风格适用于本轮所有 Agent，但不得覆盖安全、事实准确性和引用要求。"
        )

    def _memory(
        self,
        user_id: str,
        conversation_id: str | None,
        agent_id: str | None,
        question: str,
        created_at: datetime | None = None,
    ) -> UserQuestionMemory:
        intent = intent_service.classify(question)
        words = re.findall(
            r"[A-Za-z][A-Za-z0-9_+\-]{1,24}|[\u4e00-\u9fff]{2,8}",
            question,
        )
        stop = {
            "帮我",
            "一下",
            "这个",
            "可以",
            "应该",
            "现在",
            "需要",
            "如何",
            "什么",
            "我的",
            "进行",
            "实现",
        }
        keywords = [
            word.lower()
            for word in words
            if word.lower() not in stop and len(word.strip()) > 1
        ][:10]
        item = UserQuestionMemory(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            question=question,
            category=intent.category,
            keywords_json=dumps(list(dict.fromkeys(keywords))),
        )
        if created_at is not None:
            item.created_at = created_at
            item.updated_at = created_at
        return item

    async def remember_question(
        self,
        db: AsyncSession,
        *,
        user_id: str | None,
        conversation_id: str,
        agent_id: str,
        question: str,
    ) -> None:
        if not user_id:
            return
        preference = await self.preference(db, user_id)
        if preference.memory_enabled:
            db.add(
                self._memory(
                    user_id,
                    conversation_id,
                    agent_id,
                    question,
                )
            )
            await db.flush()

    async def profile(self, db: AsyncSession, user_id: str) -> dict[str, Any]:
        memories = list(
            (
                await db.scalars(
                    select(UserQuestionMemory)
                    .where(UserQuestionMemory.user_id == user_id)
                    .order_by(desc(UserQuestionMemory.created_at))
                )
            ).all()
        )
        category_counts = Counter(item.category for item in memories)
        keyword_counts: Counter[str] = Counter()
        agent_counts = Counter(item.agent_id for item in memories if item.agent_id)
        for item in memories:
            keyword_counts.update(loads(item.keywords_json, []))
        agent_ids = list(agent_counts)
        agents = (
            {
                item.id: item.name
                for item in (
                    await db.scalars(
                        select(AgentDefinition).where(AgentDefinition.id.in_(agent_ids))
                    )
                ).all()
            }
            if agent_ids
            else {}
        )
        traits: list[str] = []
        if category_counts["web_research"] + category_counts["knowledge_query"] >= 2:
            traits.append("重视资料检索与证据")
        if (
            category_counts["local_file_access"]
            + category_counts["local_workspace_change"]
            + category_counts["command_execution"]
            >= 2
        ):
            traits.append("经常处理本地项目与文件")
        average_length = round(
            sum(len(item.question) for item in memories) / max(1, len(memories)), 1
        )
        if average_length >= 45:
            traits.append("倾向提供完整上下文")
        elif memories:
            traits.append("偏好直接表达任务目标")
        if len(category_counts) >= 4:
            traits.append("关注主题较为多元")
        if not traits:
            traits.append("画像仍在学习中")
        profile = {
            "question_count": len(memories),
            "average_question_length": average_length,
            "traits": traits,
            "top_topics": [
                {"name": name, "count": count}
                for name, count in keyword_counts.most_common(12)
            ],
            "intent_distribution": [
                {"name": name, "count": count}
                for name, count in category_counts.most_common()
            ],
            "favorite_agents": [
                {"name": agents.get(agent_id, "历史 Agent"), "count": count}
                for agent_id, count in agent_counts.most_common(5)
            ],
            "recent_questions": [
                {
                    "id": item.id,
                    "question": item.question,
                    "category": item.category,
                    "created_at": item.created_at,
                }
                for item in memories[:10]
            ],
            "updated_at": datetime.now(timezone.utc),
        }
        preference = await self.preference(db, user_id)
        preference.profile_json = dumps(profile)
        await db.flush()
        return profile

    async def usage(
        self, db: AsyncSession, user_id: str, range_name: str
    ) -> dict[str, Any]:
        range_name = range_name if range_name in {"day", "week", "month"} else "day"
        now = datetime.now(timezone.utc)
        start = {
            "day": now - timedelta(days=6),
            "week": now - timedelta(weeks=7),
            "month": now - timedelta(days=365),
        }[range_name]
        runs = list(
            (
                await db.scalars(
                    select(AgentRun)
                    .where(
                        AgentRun.user_id == user_id,
                        AgentRun.created_at >= start,
                    )
                    .order_by(desc(AgentRun.created_at))
                )
            ).all()
        )
        all_runs = list(
            (
                await db.scalars(
                    select(AgentRun)
                    .where(AgentRun.user_id == user_id)
                    .order_by(desc(AgentRun.created_at))
                )
            ).all()
        )
        agent_ids = list({item.agent_id for item in all_runs})
        agents = {
            item.id: item.name
            for item in (
                await db.scalars(
                    select(AgentDefinition).where(AgentDefinition.id.in_(agent_ids))
                )
            ).all()
        } if agent_ids else {}

        if range_name == "day":
            keys = [
                (now.astimezone(LOCAL_TZ).date() - timedelta(days=offset))
                for offset in range(6, -1, -1)
            ]

            def key_for(value: datetime):
                return _aware(value).astimezone(LOCAL_TZ).date()

            def label_for(value):
                return value.strftime("%m/%d")
        elif range_name == "week":
            current = now.astimezone(LOCAL_TZ).date()
            monday = current - timedelta(days=current.weekday())
            keys = [monday - timedelta(weeks=offset) for offset in range(7, -1, -1)]

            def key_for(value: datetime):
                local_date = _aware(value).astimezone(LOCAL_TZ).date()
                return local_date - timedelta(days=local_date.weekday())

            def label_for(value):
                return f"{value.month}/{value.day}"
        else:
            local_now = now.astimezone(LOCAL_TZ)
            keys = []
            year, month = local_now.year, local_now.month
            for offset in range(11, -1, -1):
                index = year * 12 + month - 1 - offset
                keys.append((index // 12, index % 12 + 1))

            def key_for(value: datetime):
                local_value = _aware(value).astimezone(LOCAL_TZ)
                return local_value.year, local_value.month

            def label_for(value):
                return f"{value[0]}/{value[1]:02d}"

        buckets = {key: {"tokens": 0, "runs": 0} for key in keys}
        for run in runs:
            key = key_for(run.created_at)
            if key in buckets:
                buckets[key]["tokens"] += run.token_usage
                buckets[key]["runs"] += 1
        total_tokens = sum(item.token_usage for item in all_runs)
        completed = sum(item.status == "completed" for item in all_runs)
        visible_runs = all_runs[:30]
        visible_run_ids = [item.id for item in visible_runs]
        questions = (
            {
                item.run_id: item.content
                for item in (
                    await db.scalars(
                        select(AgentMessage).where(
                            AgentMessage.run_id.in_(visible_run_ids),
                            AgentMessage.role == "user",
                        )
                    )
                ).all()
                if item.run_id
            }
            if visible_run_ids
            else {}
        )
        return {
            "range": range_name,
            "summary": {
                "total_tokens": total_tokens,
                "period_tokens": sum(item.token_usage for item in runs),
                "total_runs": len(all_runs),
                "period_runs": len(runs),
                "average_tokens": round(total_tokens / max(1, len(all_runs))),
                "success_rate": round(completed * 100 / max(1, len(all_runs))),
            },
            "chart": [
                {
                    "label": label_for(key),
                    "tokens": buckets[key]["tokens"],
                    "runs": buckets[key]["runs"],
                }
                for key in keys
            ],
            "records": [
                {
                    "id": item.id,
                    "created_at": item.created_at,
                    "agent_name": agents.get(item.agent_id, "历史 Agent"),
                    "input": questions.get(item.id, item.input_text),
                    "status": item.status,
                    "tokens": item.token_usage,
                    "duration_ms": item.duration_ms,
                }
                for item in visible_runs
            ],
        }


user_service = UserService()
