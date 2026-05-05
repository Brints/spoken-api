"""change_auth_token_ids_to_uuid

Revision ID: 5421608b7a60
Revises: c10f646cb60e
Create Date: 2026-05-05 11:47:39.676258

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "5421608b7a60"
down_revision: Union[str, Sequence[str], None] = "c10f646cb60e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Ensure extension exists
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # Drop defaults and alter types with conversion
    op.execute("ALTER TABLE verification_tokens ALTER COLUMN id DROP DEFAULT")
    op.execute(
        "ALTER TABLE verification_tokens ALTER COLUMN id TYPE uuid USING (uuid_generate_v4())"
    )

    op.execute("ALTER TABLE password_reset_tokens ALTER COLUMN id DROP DEFAULT")
    op.execute(
        "ALTER TABLE password_reset_tokens ALTER COLUMN id TYPE uuid USING (uuid_generate_v4())"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Note: downgrading back to serial/int is non-trivial if UUIDs were generated.
    op.execute("ALTER TABLE verification_tokens ALTER COLUMN id TYPE integer USING (1)")
    op.execute(
        "ALTER TABLE password_reset_tokens ALTER COLUMN id TYPE integer USING (1)"
    )
