# === imports ===

import os, logging, importlib, time, secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Depends, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from sqlalchemy import select, func, literal, text
from sqlalchemy.orm import Session
from sqlalchemy.orm import aliased

from db import engine, SessionLocal, Base

from fastapi import Request

def _gid_from_request(request: Request) -> int:
    # Prefer header
    h = request.headers.get("x-game-instance-id")
    if h and h.isdigit():
        return int(h)
    # Try parse from /games/{gid}/... prefix
    path = request.url.path or ""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "games" and parts[1].isdigit():
        return int(parts[1])
    return 1

from sqlalchemy import text as _sql_text

def ensure_schema_mvp():
    # Create game_instance table and add game_instance_id columns if not exist (PostgreSQL)
    ddl = """
    CREATE TABLE IF NOT EXISTS game_instance (
        id BIGSERIAL PRIMARY KEY,
        room_id BIGINT,
        template_id BIGINT,
        state TEXT NOT NULL DEFAULT 'running'
    );
    ALTER TABLE IF EXISTS flag          ADD COLUMN IF NOT EXISTS game_instance_id BIGINT NOT NULL DEFAULT 1;
    ALTER TABLE IF EXISTS submission    ADD COLUMN IF NOT EXISTS game_instance_id BIGINT NOT NULL DEFAULT 1;
    ALTER TABLE IF EXISTS check_result  ADD COLUMN IF NOT EXISTS game_instance_id BIGINT NOT NULL DEFAULT 1;

    CREATE INDEX IF NOT EXISTS ix_flag_gid              ON flag (game_instance_id);
    CREATE INDEX IF NOT EXISTS ix_submission_gid        ON submission (game_instance_id);
    CREATE INDEX IF NOT EXISTS ix_check_result_gid      ON check_result (game_instance_id);
    CREATE INDEX IF NOT EXISTS ix_check_result_gid_tst  ON check_result (game_instance_id, team_id, service_id, tick);
    """
    with engine.begin() as conn:
        for stmt in ddl.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.exec_driver_sql(s)
from models import Team, Service, Flag, Submission, CheckResult, Score
from utils import gen_flag, current_tick, FLAG_RE

# —— Additional imports ——
import re, shlex, asyncio
from typing import List, Dict
from pydantic import BaseModel
from urllib.parse import urlparse
import httpx  # If missing in requirements.txt, please add it

from typing import Annotated

# === end imports ===

# --- DB dependency ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(title="EduAWD (MVP)")
scheduler = AsyncIOScheduler(timezone=timezone.utc)
TICK_SEC = 120
ATTACK_POINTS = 10

@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema_mvp()
    with SessionLocal() as db:
        team_cnt = db.execute(select(func.count(Team.id))).scalar()
        if not team_cnt:
            seed(db)
    scheduler.add_job(rotate_flags, "interval", seconds=TICK_SEC, id="rotate")
    scheduler.add_job(run_checks,   "interval", seconds=TICK_SEC, id="check")
    scheduler.start()

def seed(db: Session):
    now = datetime.now(timezone.utc)

    def ensure_team(name: str, role: str, host: str | None = None):
        t = db.execute(select(Team).where(Team.name == name)).scalar_one_or_none()
        if t is None:
            t = Team(name=name, role=role, host=host, token=secrets.token_hex(16), created_at=now)
            db.add(t); db.flush()
        return t

    def ensure_service(name: str):
        s = db.execute(select(Service).where(Service.name == name)).scalar_one_or_none()
        if s is None:
            s = Service(
                name=name, weight=1,
                checker_module="checkers.web_echo:check",
                port=80, flag_ttl_sec=TICK_SEC
            )
            db.add(s); db.flush()
        return s

    blue1 = ensure_team("blue1", "blue", host="vulnbox_blue1")
    red1  = ensure_team("red1",  "red")
    inst  = ensure_team("instructor", "instructor")
    svc   = ensure_service("echo")

    for t in (blue1, red1, inst):
        if db.execute(select(Score).where(Score.team_id == t.id)).scalar_one_or_none() is None:
            db.add(Score(team_id=t.id, sla_points=0, attack_points=0))

    db.commit()
    print("=== Seeded ===")
    for t in db.execute(select(Team)).scalars():
        print(f"Team {t.name} ({t.role}) token: {t.token}")

async def rotate_flags():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        services = db.execute(select(Service)).scalars().all()
        teams = db.execute(select(Team).where(Team.role == "blue")).scalars().all()
        for s in services:
            for t in teams:
                db.query(Flag).filter(Flag.team_id == t.id, Flag.service_id == s.id).delete()
                db.add(Flag(team_id=t.id, service_id=s.id, value=gen_flag(),
                            expires_at=now + timedelta(seconds=s.flag_ttl_sec)))
        db.commit()
    print(f"[rotate_flags] {now.isoformat()}")

async def run_checks():
    tick = current_tick()
    with SessionLocal() as db:
        services = db.execute(select(Service)).scalars().all()
        teams    = db.execute(select(Team).where(Team.role == "blue")).scalars().all()
        for s in services:
            mod_name, func_name = s.checker_module.split(":")
            check = getattr(importlib.import_module(mod_name), func_name)
            for t in teams:
                f = db.query(Flag).filter(Flag.team_id == t.id, Flag.service_id == s.id).first()
                if not f:
                    ok, details = False, "no flag"
                else:
                    try:
                        ok, details = await check(t, s, f.value)
                    except Exception as e:
                        ok, details = False, f"checker error: {type(e).__name__}: {e}"

                db.add(CheckResult(team_id=t.id, service_id=s.id, tick=tick, up=bool(ok), details=str(details)))
                if ok:
                    sc = db.get(Score, t.id)
                    if sc: sc.sla_points += s.weight
        db.commit()
    print(f"[run_checks] tick={tick} finished")

@app.get("/scoreboard")
def scoreboard(db: Session = Depends(get_db)):
    sla = func.coalesce(func.sum(getattr(Score, "sla_points", literal(0))), 0)
    atk = func.coalesce(func.sum(getattr(Score, "attack_points", literal(0))), 0)
    rows = db.execute(
        select(
            Team.id.label("team_id"),
            Team.name.label("team"),
            Team.role.label("role"),
            sla.label("sla"),
            atk.label("atk"),
            (sla + atk).label("points"),
        )
        .outerjoin(Score, Score.team_id == Team.id)
        .group_by(Team.id, Team.name, Team.role)
        .order_by((sla + atk).desc(), Team.id.asc())
    ).all()
    return [dict(r._mapping) for r in rows]

class SubmitIn(BaseModel):
    flag: str

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Header, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

def _as_aware_utc(dt):
    """Normalize possibly naive datetime to UTC with timezone for comparison."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

@app.post("/submit")
def submit_flag(
    payload: SubmitIn,
    x_team_underscore: Optional[str] = Header(None, alias="x_team_token"),
    x_team_dash: Optional[str] = Header(None, alias="X-Team-Token"),
    db: Session = Depends(get_db),
):
    x_team_token = x_team_underscore or x_team_dash
    # 1) Authentication: must be red team
    attacker = db.scalar(select(Team).where(Team.token == x_team_token))
    if not attacker:
        raise HTTPException(status_code=401, detail="bad token")
    if attacker.role != "red":
        raise HTTPException(status_code=403, detail="only red team can submit")

    flag = (payload.flag or "").strip()
    now_utc = datetime.now(timezone.utc)

    # 2) Find flag ownership (ensure f always defined)
    f: Optional[Flag] = db.scalar(select(Flag).where(Flag.value == flag))
    if not f:
        _safe_log_submission(
            db, attacker_id=attacker.id, victim_id=None,
            flag=flag, verdict="invalid", service_id=None
        )
        return {"status": "invalid", "ts": now_utc.isoformat()}

    # 3) Check duplicate submission by same attacker (accepted)
    dup = db.scalar(
        select(Submission.id)
        .where(
            Submission.attacker_team_id == attacker.id,
            Submission.flag_value == flag,
            Submission.verdict == "accepted",
        )
        .limit(1)
    )
    if dup:
        _safe_log_submission(
            db, attacker_id=attacker.id, victim_id=f.team_id,
            flag=flag, verdict="duplicate", service_id=f.service_id
        )
        return {"status": "duplicate", "ts": now_utc.isoformat()}

    # 4) Check expiration (compare in UTC)
    exp_utc = _as_aware_utc(getattr(f, "expires_at", None))
    if exp_utc is not None and exp_utc <= now_utc:
        _safe_log_submission(
            db, attacker_id=attacker.id, victim_id=f.team_id,
            flag=flag, verdict="expired", service_id=f.service_id
        )
        return {"status": "expired", "ts": now_utc.isoformat()}

    # 5) Scoring
    weight = 1
    svc_weight = db.scalar(select(Service.weight).where(Service.id == f.service_id))
    if svc_weight:
        weight = int(svc_weight)

    sc = db.scalar(select(Score).where(Score.team_id == attacker.id).with_for_update())
    if not sc:
        sc = Score(team_id=attacker.id, sla_points=0, attack_points=0)
        db.add(sc)
        db.flush()
    sc.attack_points = (sc.attack_points or 0) + weight

    # 6) Log accepted submission
    _safe_log_submission(
        db, attacker_id=attacker.id, victim_id=f.team_id,
        flag=flag, verdict="accepted", service_id=f.service_id
    )

    # 7) Commit transaction
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        print("[submit] commit failed:", e)
        raise HTTPException(status_code=500, detail="database commit failed")

    return {"status": "accepted", "points": weight, "ts": now_utc.isoformat()}

# Log submission safely
def _safe_log_submission(
    db: Session,
    attacker_id: int | None,
    victim_id: int | None,
    flag: str,
    verdict: str,
    service_id: int | None = None,
) -> None:
    """
    Log one submission into the submission table:
    - Matches models.Submission columns: flag_value, (optional) service_id / victim_team_id / verdict
    - Rollback the session on error to avoid InFailedSqlTransaction during subsequent commit.
    """
    try:
        db.execute(
            text("""
                INSERT INTO submission(
                    attacker_team_id,
                    victim_team_id,
                    service_id,
                    flag_value,
                    verdict,
                    created_at
                )
                VALUES (:a, :v, :s, :flag, :verdict, NOW())
            """),
            {
                "a": attacker_id,
                "v": victim_id,
                "s": service_id,
                "flag": flag,
                "verdict": verdict,
            },
        )
    except Exception as e:
        # Important: rollback to recover the session for further scoring updates / commits
        db.rollback()
        print("[submit] skip writing submission:", e)

# Admin authentication
def require_admin(
    x_admin_dash: Optional[str] = Header(None, alias="x-admin-token"),
    x_admin_underscore: Optional[str] = Header(None, alias="x_admin_token"),
):
    token = x_admin_dash or x_admin_underscore
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(status_code=401, detail="bad admin token")
    return True

@app.post("/admin/rotate_now", dependencies=[Depends(require_admin)])
async def admin_rotate_now():
    await rotate_flags()
    return {"ok": True, "tick": current_tick()}

@app.post("/admin/check_now", dependencies=[Depends(require_admin)])
async def admin_check_now():
    await run_checks()
    return {"ok": True, "tick": current_tick()}

# Recent submissions / last checks (used by admin UI)
@app.get("/admin/recent_submissions", dependencies=[Depends(require_admin)])
def recent_submissions(db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Submission.created_at.label("ts"),
            Team.name.label("attacker"),
            Submission.victim_team_id,
            Submission.verdict,
            Submission.flag_value,
            Submission.service_id,
        )
        .join(Team, Team.id == Submission.attacker_team_id)
        .order_by(Submission.id.desc())
        .limit(30)
    ).all()
    out = []
    for r in rows:
        out.append({
            "ts": r.ts.isoformat(),
            "attacker": r.attacker,
            "victim": (db.get(Team, r.victim_team_id).name if r.victim_team_id else None),
            "service": (db.get(Service, r.service_id).name if r.service_id else None),
            "verdict": r.verdict,
            "flag": (r.flag_value[:16] + "…") if r.flag_value else None,
        })
    return out

# ================= Red team execution interface =================
@app.get("/admin/last_checks", dependencies=[Depends(require_admin)])
def admin_last_checks(db: Session = Depends(get_db)):
    latest = (
        select(
            CheckResult.team_id.label("team_id"),
            CheckResult.service_id.label("service_id"),
            func.max(CheckResult.tick).label("tick"),
        )
        .group_by(CheckResult.team_id, CheckResult.service_id)
        .subquery()
    )
    CR = aliased(CheckResult)
    rows = db.execute(
        select(
            Team.name.label("team"),
            Service.name.label("service"),
            CR.up.label("ok"),
            CR.tick.label("tick"),
        )
        .select_from(latest)
        .join(
            CR,
            (CR.team_id == latest.c.team_id)
            & (CR.service_id == latest.c.service_id)
            & (CR.tick == latest.c.tick)
        )
        .join(Team, Team.id == latest.c.team_id)
        .join(Service, Service.id == latest.c.service_id)
        .order_by(Team.name, Service.name)
    ).all()
    return [{"team": r.team, "service": r.service, "ok": bool(r.ok), "tick": int(r.tick)} for r in rows]



def require_red(
    db: Session = Depends(get_db),
    x_team_dash: Optional[str] = Header(None, alias="X-Team-Token"),
    x_team_underscore: Optional[str] = Header(None, alias="x_team_token"),
):
    x_team_token = x_team_dash or x_team_underscore
    if not x_team_token:
        raise HTTPException(status_code=401, detail="Missing X-Team-Token")
    team = db.execute(select(Team).where(Team.token == x_team_token)).scalar_one_or_none()
    if not team or team.role != "red":
        raise HTTPException(status_code=401, detail="Not a red team token")
    return team

def _allowed_targets(db: Session) -> Dict[str, str]:
    rows = db.execute(select(Team).where(Team.role == "blue")).scalars().all()
    # Allow using either team name (blue1) or host (vulnbox_blue1)
    return {t.name: t.host for t in rows if t.host}



class RedExecReq(BaseModel):
    cmd: str

@app.post("/red/exec")
async def red_exec(
    body: RedExecReq,
    red_team: Team = Depends(require_red),
    db: Session = Depends(get_db),
):
    cmd = (body.cmd or "").strip()
    if not cmd:
        raise HTTPException(400, "empty command")

    # Block dangerous operators
    if re.search(r"[;&|`>$]", cmd):
        raise HTTPException(400, "operators like ; | & ` > $ are not allowed")

    parts = shlex.split(cmd)
    if not parts:
        raise HTTPException(400, "invalid command")
    prog = parts[0]

    targets = _allowed_targets(db)
    allowed_hosts = set(targets.values()) | set(targets.keys())

    if prog == "curl":
        # curl [-X METHOD] [-H 'K: V'] [-d DATA] URL
        method = "GET"
        data = None
        headers = {}
        url = None
        i = 1
        while i < len(parts):
            p = parts[i]
            if p in ("-X", "--request") and i + 1 < len(parts):
                method = parts[i + 1].upper()
                i += 2
                continue
            if p in ("-d", "--data", "--data-raw") and i + 1 < len(parts):
                data = parts[i + 1]
                if method == "GET":
                    method = "POST"
                i += 2
                continue
            if p in ("-H", "--header") and i + 1 < len(parts):
                kv = parts[i + 1]
                if ":" in kv:
                    k, v = kv.split(":", 1)
                    headers[k.strip()] = v.strip()
                i += 2
                continue
            if p.startswith("-"):
                i += 1
                continue
            url = p
            i += 1

        if not url:
            raise HTTPException(400, "curl requires URL")

        u = urlparse(url)
        host = u.hostname or ""
        if host in targets:
            host = targets[host]
        if host not in allowed_hosts:
            raise HTTPException(400, f"target not allowed: {u.hostname}")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.request(method, url, headers=headers, content=data)
            return {
                "ok": True,
                "kind": "curl",
                "status": resp.status_code,
                "headers": dict(resp.headers),
                "stdout": resp.text[:65536],
            }
        except Exception as e:
            raise HTTPException(500, f"curl error: {e}")

    elif prog in ("nc", "ncat", "netcat"):
        # nc host port [payload]
        if len(parts) < 3:
            raise HTTPException(400, "usage: nc host port [payload]")
        host = parts[1]
        port = int(parts[2])
        payload = " ".join(parts[3:]) if len(parts) > 3 else ""

        if host in targets:
            host = targets[host]
        if host not in allowed_hosts:
            raise HTTPException(400, f"target not allowed: {host}")

        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3.0)
            if payload:
                # Support escape sequences like \n or \r\n
                to_send = payload.encode().decode("unicode_escape").encode()
                writer.write(to_send)
                await writer.drain()
            try:
                data = await asyncio.wait_for(reader.read(65536), timeout=3.0)
            except asyncio.TimeoutError:
                data = b""
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return {"ok": True, "kind": "nc", "stdout": data.decode(errors="replace")}
        except Exception as e:
            raise HTTPException(500, f"nc error: {e}")

    else:
        raise HTTPException(400, "only curl / nc are allowed")

# ========== Blue team endpoints ==========
def require_team(
    x_team_underscore: Optional[str] = Header(None, alias="x_team_token"),
    x_team_dash: Optional[str] = Header(None, alias="X-Team-Token"),
):
    x_team_token = x_team_underscore or x_team_dash
    with SessionLocal() as db:
        team = db.execute(select(Team).where(Team.token == x_team_token)).scalar_one_or_none()
        if not team:
            raise HTTPException(status_code=401, detail="bad team token")
        return team

@app.get("/blue/me")
def blue_me(team: Team = Depends(require_team)):
    return {
        "id": team.id,
        "name": team.name,
        "role": team.role,
        "host": team.host,
    }

@app.get("/blue/last_checks")
def blue_last_checks(team: Team = Depends(require_team), db: Session = Depends(get_db)):
    # Get the latest tick for each service for this team
    latest = (
        select(
            CheckResult.service_id.label("service_id"),
            func.max(CheckResult.tick).label("max_tick"),
        )
        .where(CheckResult.team_id == team.id)
        .group_by(CheckResult.service_id)
        .subquery()
    )
    CR = aliased(CheckResult)
    rows = db.execute(
        select(
            Service.name.label("service"),
            CR.up.label("ok"),
            CR.tick.label("tick"),
            CR.details.label("details"),
        )
        .select_from(latest)
        .join(CR,
              (CR.service_id == latest.c.service_id) &
              (CR.tick == latest.c.max_tick) &
              (CR.team_id == team.id))
        .join(Service, Service.id == latest.c.service_id)
        .order_by(Service.name)
    ).all()

    return [
        {
            "service": r.service,
            "ok": bool(r.ok),
            "tick": int(r.tick) if r.tick is not None else None,
            "details": r.details,
        }
        for r in rows
    ]

@app.get("/blue/recent_attacks")
def blue_recent_attacks(team: Team = Depends(require_team), db: Session = Depends(get_db)):
    # Attacks where this team is the victim
    rows = db.execute(
        select(
            Submission.created_at.label("time"),
            Team.name.label("attacker"),
            Service.name.label("service"),
            Submission.verdict.label("verdict"),
            Submission.flag_value.label("flag_value"),
        )
        .select_from(Submission)
        .join(Team, Team.id == Submission.attacker_team_id)
        .join(Service, Service.id == Submission.service_id)
        .where(Submission.victim_team_id == team.id)
        .order_by(Submission.created_at.desc())
        .limit(20)
    ).all()

    return [
        {
            "time": r.time.isoformat() if r.time else None,
            "attacker": r.attacker,
            "service": r.service,
            "verdict": r.verdict,
            "flag": (r.flag_value[:16] + "…") if r.flag_value else None,
        }
        for r in rows
    ]


@app.get("/healthz")
def healthz():
    return {"ok": True}


# Static Admin UI mounts
app.mount("/admin", StaticFiles(directory=Path(__file__).parent / "admin_ui", html=True), name="admin")
app.mount("/red", StaticFiles(directory=Path(__file__).parent / "red_ui", html=True), name="red")
app.mount("/blue", StaticFiles(directory=Path(__file__).parent / "blue_ui", html=True), name="blue")


# ================= Multi-Instance Namespace (MVP) =================
from sqlalchemy.orm import aliased
from models import GameInstance

@app.post("/games", dependencies=[Depends(require_admin)])
def create_game_instance(db: Session = Depends(get_db)):
    gi = GameInstance()
    db.add(gi); db.commit(); db.refresh(gi)
    return {"game_instance_id": gi.id}

@app.get("/games/{gid}/admin/recent_submissions", dependencies=[Depends(require_admin)])
def ns_recent_submissions(gid: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            Submission.created_at.label("ts"),
            Team.name.label("attacker"),
            Submission.victim_team_id,
            Submission.verdict,
            Submission.flag_value,
            Submission.service_id,
        )
        .join(Team, Team.id == Submission.attacker_team_id)
        .where(Submission.game_instance_id == gid)
        .order_by(Submission.id.desc())
        .limit(30)
    ).all()
    out = []
    for r in rows:
        out.append({
            "ts": r.ts.isoformat(),
            "attacker": r.attacker,
            "victim": None if r.victim_team_id is None else db.get(Team, r.victim_team_id).name,
            "verdict": r.verdict,
            "flag": (r.flag_value[:16] + "…") if r.flag_value else None,
            "service": None if r.service_id is None else db.get(Service, r.service_id).name,
        })
    return out

@app.get("/games/{gid}/admin/last_checks", dependencies=[Depends(require_admin)])
def ns_admin_last_checks(gid: int, db: Session = Depends(get_db)):
    latest = (
        select(
            CheckResult.team_id.label("team_id"),
            CheckResult.service_id.label("service_id"),
            func.max(CheckResult.tick).label("tick"),
        )
        .where(CheckResult.game_instance_id == gid)
        .group_by(CheckResult.team_id, CheckResult.service_id)
        .subquery()
    )
    CR = aliased(CheckResult)
    rows = db.execute(
        select(
            Team.name.label("team"),
            Service.name.label("service"),
            CR.up.label("ok"),
            CR.tick.label("tick"),
        )
        .select_from(latest)
        .join(
            CR,
            (CR.team_id == latest.c.team_id)
            & (CR.service_id == latest.c.service_id)
            & (CR.tick == latest.c.tick)
            & (CR.game_instance_id == gid)
        )
        .join(Team, Team.id == latest.c.team_id)
        .join(Service, Service.id == latest.c.service_id)
        .order_by(Team.name, Service.name)
    ).all()
    return [dict(r._mapping) for r in rows]

@app.get("/games/{gid}/blue/last_checks")
def ns_blue_last_checks(gid: int, team: Team = Depends(require_team), db: Session = Depends(get_db)):
    latest = (
        select(
            CheckResult.service_id.label("service_id"),
            func.max(CheckResult.tick).label("tick"),
        )
        .where(CheckResult.team_id == team.id, CheckResult.game_instance_id == gid)
        .group_by(CheckResult.service_id)
        .subquery()
    )
    CR = aliased(CheckResult)
    rows = db.execute(
        select(
            Service.name.label("service"),
            CR.up.label("ok"),
            CR.tick.label("tick"),
        )
        .select_from(latest)
        .join(
            CR,
            (CR.service_id == latest.c.service_id) & (CR.tick == latest.c.tick) & (CR.game_instance_id == gid)
        )
        .join(Service, Service.id == latest.c.service_id)
        .order_by(Service.name)
    ).all()
    return [dict(r._mapping) for r in rows]

class SubmitInNS(BaseModel):
    flag: str

@app.post("/games/{gid}/submit")
def ns_submit_flag(gid: int, payload: SubmitInNS, db: Session = Depends(get_db), team: Team = Depends(require_red)):
    now_utc = datetime.now(timezone.utc)
    flag = payload.flag.strip()

    # 1) Valid format (reuse FLAG_RE)
    if not FLAG_RE.match(flag):
        _safe_log_submission(db, attacker_id=team.id, victim_id=None, flag=flag, verdict="invalid", service_id=None, gid=gid)
        return {"status": "invalid", "ts": now_utc.isoformat()}

    # 2) Find flag ownership WITHIN THIS INSTANCE
    f: Optional[Flag] = db.scalar(select(Flag).where(Flag.value == flag, Flag.game_instance_id == gid))
    if not f:
        _safe_log_submission(db, attacker_id=team.id, victim_id=None, flag=flag, verdict="invalid", service_id=None, gid=gid)
        return {"status": "invalid", "ts": now_utc.isoformat()}

    # 3) Duplicate by same attacker on this instance
    dup = db.scalar(
        select(Submission.id)
        .where(Submission.attacker_team_id == team.id, Submission.flag_value == flag, Submission.verdict == "accepted", Submission.game_instance_id == gid)
        .limit(1)
    )
    if dup:
        _safe_log_submission(db, attacker_id=team.id, victim_id=f.team_id, flag=flag, verdict="duplicate", service_id=f.service_id, gid=gid)
        return {"status": "duplicate", "ts": now_utc.isoformat()}

    # 4) Check expiration
    exp_utc = _as_aware_utc(getattr(f, "expires_at", None))
    if exp_utc is not None and exp_utc <= now_utc:
        _safe_log_submission(db, attacker_id=team.id, victim_id=f.team_id, flag=flag, verdict="expired", service_id=f.service_id, gid=gid)
        return {"status": "expired", "ts": now_utc.isoformat()}

    # 5) Accept
    _safe_log_submission(db, attacker_id=team.id, victim_id=f.team_id, flag=flag, verdict="accepted", service_id=f.service_id, gid=gid)
    return {"status": "accepted", "ts": now_utc.isoformat()}

def _safe_log_submission(db: Session, attacker_id: int, victim_id: Optional[int], flag: str, verdict: str, service_id: Optional[int], gid: int = 1):
    db.add(Submission(attacker_team_id=attacker_id, victim_team_id=victim_id, flag_value=flag, verdict=verdict, service_id=service_id, game_instance_id=gid))
    db.commit()

@app.get("/games/{gid}/scoreboard")
def ns_scoreboard(gid: int, db: Session = Depends(get_db)):
    # SLA points = sum over check_results where up=true times service.weight
    sla_rows = db.execute(
        select(CheckResult.team_id, func.sum(Service.weight))
        .join(Service, Service.id == CheckResult.service_id)
        .where(CheckResult.game_instance_id == gid, CheckResult.up == True)
        .group_by(CheckResult.team_id)
    ).all()
    sla_map = {r[0]: int(r[1] or 0) for r in sla_rows}

    # Attack points = sum of accepted submissions * service.weight by attacker
    atk_rows = db.execute(
        select(Submission.attacker_team_id, func.sum(Service.weight))
        .join(Service, Service.id == Submission.service_id)
        .where(Submission.game_instance_id == gid, Submission.verdict == "accepted")
        .group_by(Submission.attacker_team_id)
    ).all()
    atk_map = {r[0]: int(r[1] or 0) for r in atk_rows}

    rows = db.execute(select(Team)).scalars().all()
    out = []
    for t in rows:
        sla = sla_map.get(t.id, 0)
        atk = atk_map.get(t.id, 0)
        out.append({
            "team_id": t.id,
            "team": t.name,
            "role": t.role,
            "sla": sla,
            "atk": atk,
            "points": sla + atk,
        })
    out.sort(key=lambda x: (-x["points"], x["team_id"]))
    return out

# Manual per-instance rotate and check (admin)
@app.post("/games/{gid}/admin/rotate_now", dependencies=[Depends(require_admin)])
def ns_rotate_now(gid: int, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    services = db.execute(select(Service)).scalars().all()
    teams = db.execute(select(Team).where(Team.role == "blue")).scalars().all()
    for s in services:
        for t in teams:
            db.add(Flag(team_id=t.id, service_id=s.id, value=gen_flag(), expires_at=now + timedelta(seconds=s.flag_ttl_sec), game_instance_id=gid))
    db.commit()
    return {"ok": True}

@app.post("/games/{gid}/admin/check_now", dependencies=[Depends(require_admin)])
async def ns_check_now(gid: int, db: Session = Depends(get_db)):
    tick = current_tick(TICK_SEC)
    services = db.execute(select(Service)).scalars().all()
    teams = db.execute(select(Team).where(Team.role == "blue")).scalars().all()
    for s in services:
        mod_name, func_name = s.checker_module.split(":")
        check = getattr(importlib.import_module(mod_name), func_name)
        for t in teams:
            f = db.execute(
                select(Flag).where(Flag.team_id == t.id, Flag.service_id == s.id, Flag.game_instance_id == gid).order_by(Flag.id.desc()).limit(1)
            ).scalar_one_or_none()
            if not f:
                ok, details = False, "no flag"
            else:
                try:
                    ok, details = await check(t, s, f.value)
                except Exception as e:
                    ok, details = False, f"checker error: {type(e).__name__}: {e}"
            db.add(CheckResult(team_id=t.id, service_id=s.id, tick=tick, up=bool(ok), details=str(details), game_instance_id=gid))
    db.commit()
    return {"ok": True, "tick": tick}
