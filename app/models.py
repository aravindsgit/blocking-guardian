"""Database table and connection."""
import os
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def database_url():
    url = os.getenv("DATABASE_URL", "sqlite:///./guardian.db")
    # Neon and others give postgresql://; SQLAlchemy needs the psycopg driver named.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


_url = database_url()
engine = create_engine(
    _url, pool_pre_ping=True, pool_recycle=300,
    connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    scenario: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(10), default="approval")
    seed: Mapped[int] = mapped_column(Integer, default=7)
    sessions: Mapped[list] = mapped_column(JSON)
    rejected: Mapped[list] = mapped_column(JSON, default=list)
    reviews: Mapped[dict] = mapped_column(JSON, default=dict)
    audit: Mapped[list] = mapped_column(JSON, default=list)


def init_db():
    Base.metadata.create_all(engine)
