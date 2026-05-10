# /app/models.py
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Integer, String, DateTime, ForeignKey, Text, Boolean
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# -------------------- Team --------------------
class Team(Base):
    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # blue / red / instructor
    host: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

# -------------------- Service --------------------
class Service(Base):
    __tablename__ = "service"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    checker_module: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. "checkers.web_echo:check"
    port: Mapped[int] = mapped_column(Integer, default=80, nullable=False)
    flag_ttl_sec: Mapped[int] = mapped_column(Integer, default=120, nullable=False)

# -------------------- Flag --------------------
class Flag(Base):
    __tablename__ = "flag"
    game_instance_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service.id"), nullable=False)
    value: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

# -------------------- Submission --------------------
class Submission(Base):
    __tablename__ = "submission"
    game_instance_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    attacker_team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=False)
    victim_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("team.id"), nullable=True)
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("service.id"), nullable=True)
    flag_value: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    verdict: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # accepted / rejected / expired

# -------------------- CheckResult --------------------
class CheckResult(Base):
    __tablename__ = "check_result"
    game_instance_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), nullable=False)
    service_id: Mapped[int] = mapped_column(ForeignKey("service.id"), nullable=False)
    tick: Mapped[int] = mapped_column(Integer, nullable=False)
    up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

# -------------------- Score --------------------
class Score(Base):
    __tablename__ = "score"

    team_id: Mapped[int] = mapped_column(ForeignKey("team.id"), primary_key=True)
    sla_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attack_points: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# -------------------- GameInstance --------------------
class GameInstance(Base):
    __tablename__ = "game_instance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running/ended/failed
    # runtime_info / started_at / ended_at could move to a separate table or JSON extension; simplified for now
