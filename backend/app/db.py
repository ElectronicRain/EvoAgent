from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    from . import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # create_all does not add columns to databases created by earlier EvoAgent versions.
        # These additive migrations keep existing local knowledge bases usable.
        migrations = {
            "agents": {
                "group_id": "VARCHAR(36)",
                "image_model_endpoint_id": "VARCHAR(36)",
                "rag_config_json": "TEXT NOT NULL DEFAULT '{}'",
                "generation_config_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "model_endpoints": {
                "modality": "VARCHAR(30) NOT NULL DEFAULT 'chat'",
            },
            "agent_runs": {
                "security_json": "TEXT NOT NULL DEFAULT '{}'",
                "user_id": "VARCHAR(36)",
            },
            "agent_conversations": {
                "user_id": "VARCHAR(36)",
            },
            "evolution_proposals": {
                "goal_json": "TEXT NOT NULL DEFAULT '{}'",
                "config_json": "TEXT NOT NULL DEFAULT '{}'",
                "decision_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "evaluation_cases": {
                "category": "VARCHAR(60) NOT NULL DEFAULT 'quality'",
                "weight": "FLOAT NOT NULL DEFAULT 1.0",
                "enabled": "BOOLEAN NOT NULL DEFAULT 1",
            },
            "approvals": {
                "execution_result_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "skills": {
                "validation_status": "VARCHAR(30) NOT NULL DEFAULT 'pending'",
                "risk_level": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
                "validation_json": "TEXT NOT NULL DEFAULT '{}'",
                "content_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
                "verified_at": "DATETIME",
            },
            "knowledge_documents": {
                "source_id": "VARCHAR(36)",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
                "cleaning_stats_json": "TEXT NOT NULL DEFAULT '{}'",
                "status": "VARCHAR(30) NOT NULL DEFAULT 'ready'",
            },
            "knowledge_chunks": {
                "parent_chunk_id": "VARCHAR(36)",
                "level": "VARCHAR(20) NOT NULL DEFAULT 'child'",
                "token_count": "INTEGER NOT NULL DEFAULT 0",
                "content_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            },
            "workflow_runs": {
                "control_json": "TEXT NOT NULL DEFAULT '{}'",
                "iteration_count": "INTEGER NOT NULL DEFAULT 0",
                "current_node_id": "VARCHAR(120)",
            },
        }
        for table_name, columns in migrations.items():
            existing = {
                row[1]
                for row in (await connection.execute(text(f"PRAGMA table_info({table_name})"))).all()
            }
            for column_name, definition in columns.items():
                if column_name not in existing:
                    await connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                    )
        await connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
                USING fts5(chunk_id UNINDEXED, title, content, tokenize='unicode61')
                """
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agents_group_id "
                "ON agents(group_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_model_endpoints_modality "
                "ON model_endpoints(modality)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_runs_user_id "
                "ON agent_runs(user_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_skills_validation_status "
                "ON skills(validation_status)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_agent_conversations_user_id "
                "ON agent_conversations(user_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_documents_source_id "
                "ON knowledge_documents(source_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_parent_chunk_id "
                "ON knowledge_chunks(parent_chunk_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workflow_artifacts_workflow_id "
                "ON workflow_artifacts(workflow_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workflow_artifacts_run_id "
                "ON workflow_artifacts(run_id)"
            )
        )


async def close_db() -> None:
    await engine.dispose()
