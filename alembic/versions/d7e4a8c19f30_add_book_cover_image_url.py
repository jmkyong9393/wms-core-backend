"""add book cover image URL

Revision ID: d7e4a8c19f30
Revises: c3a91e7f2b64
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d7e4a8c19f30"
down_revision: str | Sequence[str] | None = "c3a91e7f2b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an optional cover image URL to the book master."""
    op.add_column(
        "books",
        sa.Column(
            "cover_image_url",
            sa.String(length=1000),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove the book cover image URL."""
    op.drop_column("books", "cover_image_url")
