"""Persist semantic embedding spaces, freshness and resumable index jobs.

Revision ID: 0019_embedding_spaces
Revises: 0018_provider_operation_profiles
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_embedding_spaces"
down_revision: str | None = "0018_provider_operation_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name, text_column in (
        ("messages", "normalized_text"),
        ("knowledge_items", "content"),
    ):
        op.add_column(table_name, sa.Column("embedding_source_hash", sa.String(64), nullable=True))
        _backfill_source_hashes(table_name, text_column)
        # Fixed schema identifiers, fingerprints are repaired in a separate hash-only write
        op.execute(
            sa.text(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table_name}_embedding_hash AFTER UPDATE OF {text_column} ON {table_name} WHEN NEW.{text_column} IS NOT OLD.{text_column} BEGIN UPDATE {table_name} SET embedding_source_hash=NULL WHERE id=NEW.id; END"  # noqa: E501, S608
            )
        )
    op.create_table(
        "embedding_spaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("provider_profile", sa.String(64), nullable=False),
        sa.Column("provider_kind", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_embedding_spaces_config_hash", "embedding_spaces", ["config_hash"])
    with op.batch_alter_table("embeddings") as batch:
        batch.add_column(sa.Column("space_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_hash", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "provenance_status",
                sa.String(32),
                nullable=False,
                server_default="legacy_unverified",
            )
        )
        batch.create_foreign_key("fk_embeddings_space_id", "embedding_spaces", ["space_id"], ["id"])
        batch.create_unique_constraint(
            "uq_embeddings_target_space", ["target_type", "target_id", "space_id"]
        )
        batch.create_index(
            "ix_embeddings_space_scope", ["space_id", "chat_id", "message_thread_id"]
        )
    state = op.create_table(
        "embedding_routing_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.bulk_insert(state, [{"id": 1, "epoch": 0, "content_revision": 0}])
    op.create_table(
        "embedding_index_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("provider_profile", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("space_id", sa.Integer(), sa.ForeignKey("embedding_spaces.id"), nullable=True),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("settings_snapshot", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("processed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_fence", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_embedding_jobs_scope_status",
        "embedding_index_jobs",
        ["chat_id", "message_thread_id", "status"],
    )


def _backfill_source_hashes(table_name: str, text_column: str) -> None:
    if op.get_context().as_sql:
        return
    connection = op.get_bind()
    source = sa.table(
        table_name,
        sa.column("id", sa.Integer()),
        sa.column(text_column, sa.Text()),
        sa.column("embedding_source_hash", sa.String(64)),
    )
    cursor: int | None = None
    while True:
        statement = sa.select(source.c.id, source.c[text_column]).order_by(source.c.id).limit(256)
        if cursor is not None:
            statement = statement.where(source.c.id > cursor)
        rows = connection.execute(statement).all()
        if not rows:
            break
        connection.execute(
            sa.update(source)
            .where(source.c.id == sa.bindparam("source_id"))
            .values(embedding_source_hash=sa.bindparam("source_hash")),
            [
                {
                    "source_id": row.id,
                    "source_hash": hashlib.sha256(row[1].encode("utf-8")).hexdigest(),
                }
                for row in rows
            ],
        )
        cursor = rows[-1].id


def downgrade() -> None:
    op.drop_table("embedding_index_jobs")
    op.drop_table("embedding_routing_state")
    with op.batch_alter_table("embeddings") as batch:
        batch.drop_index("ix_embeddings_space_scope")
        batch.drop_constraint("uq_embeddings_target_space", type_="unique")
        batch.drop_constraint("fk_embeddings_space_id", type_="foreignkey")
        batch.drop_column("provenance_status")
        batch.drop_column("source_hash")
        batch.drop_column("space_id")
    op.drop_table("embedding_spaces")
    for table_name in ("messages", "knowledge_items"):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table_name}_embedding_hash"))
        op.drop_column(table_name, "embedding_source_hash")
