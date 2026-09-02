"""add LPN barcode sequence

Revision ID: c3a91e7f2b64
Revises: 8f6b1c2d4e5a
Create Date: 2026-08-07

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3a91e7f2b64"
down_revision: str | Sequence[str] | None = "8f6b1c2d4e5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the global sequence used for new LPN barcodes."""
    op.execute(
        sa.text(
            """
            CREATE SEQUENCE lpn_barcode_sequence
            START WITH 1
            INCREMENT BY 1
            NO CYCLE
            """
        )
    )


def downgrade() -> None:
    """Remove the global LPN barcode sequence."""
    op.execute(sa.text("DROP SEQUENCE IF EXISTS lpn_barcode_sequence"))
