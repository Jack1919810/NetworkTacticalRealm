# api/db.py
import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# Read connection string from environment (set in docker-compose.yml)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://eduawd:eduawd_pass@db:5432/eduawd",
)

# Create engine and session factory
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, class_=Session)

# Declarative base
Base = declarative_base()

# FastAPI dependency: provides a DB session per request
def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# db.py additions
from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey,
    UniqueConstraint, func
)
from sqlalchemy.orm import relationship

# Existing: Base, Team, Service, Score, ...

class FlagRecord(Base):
    __tablename__ = "flag_record"
    id = Column(Integer, primary_key=True)
    victim_team_id = Column(Integer, ForeignKey("team.id"), nullable=False, index=True)
    service_id     = Column(Integer, ForeignKey("service.id"), nullable=False, index=True)
    tick           = Column(Integer, nullable=False, index=True)
    flag           = Column(String(128), nullable=False, unique=True, index=True)
    created_at     = Column(DateTime, nullable=False, server_default=func.now())

    victim  = relationship("Team")
    service = relationship("Service")

    __table_args__ = (
        UniqueConstraint("victim_team_id", "service_id", "tick", name="uq_flag_slot"),
    )

# class Submission(Base):
#     __tablename__ = "submission"
#     id               = Column(Integer, primary_key=True)
#     attacker_team_id = Column(Integer, ForeignKey("team.id"), nullable=False, index=True)
#     flag_record_id   = Column(Integer, ForeignKey("flag_record.id"), nullable=False, index=True)
#     verdict          = Column(String(16), nullable=False)  # OK / DUP / OWN / INVALID / OLD (if needed in future)
#     points           = Column(Integer, nullable=False, default=0)
#     created_at       = Column(DateTime, nullable=False, server_default=func.now())
#
#     __table_args__ = (
#         UniqueConstraint("attacker_team_id", "flag_record_id", name="uq_once_per_attacker"),
#     )
