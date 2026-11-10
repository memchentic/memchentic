"""Declarative base class for every SQLAlchemy model."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base; every model registers on ``Base.metadata``."""
