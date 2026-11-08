"""encrypt internal signing keys; store auth codes hashed

Revision ID: 009
Revises: 008
Create Date: 2026-11-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from memchentic.auth.tokens import decrypt_private_key, encrypt_private_key
from memchentic.settings import get_settings

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settings = get_settings()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, private_pem FROM internal_signing_keys")).all()
    for row_id, pem in rows:
        if "ENCRYPTED PRIVATE KEY" in pem:
            continue
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise RuntimeError(f"internal_signing_keys {row_id} is not an RSA key")
        conn.execute(
            sa.text("UPDATE internal_signing_keys SET private_pem = :pem WHERE id = :id"),
            {"pem": encrypt_private_key(key, settings), "id": row_id},
        )

    # Codes live 10 minutes; plaintext in-flight codes are dropped, not hashed.
    op.execute("DELETE FROM auth_codes")
    op.alter_column("auth_codes", "code", new_column_name="code_hash")


def downgrade() -> None:
    settings = get_settings()
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, private_pem FROM internal_signing_keys")).all()
    for row_id, pem in rows:
        if "ENCRYPTED PRIVATE KEY" not in pem:
            continue
        plain = decrypt_private_key(pem, settings).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        conn.execute(
            sa.text("UPDATE internal_signing_keys SET private_pem = :pem WHERE id = :id"),
            {"pem": plain.decode(), "id": row_id},
        )

    op.execute("DELETE FROM auth_codes")
    op.alter_column("auth_codes", "code_hash", new_column_name="code")
