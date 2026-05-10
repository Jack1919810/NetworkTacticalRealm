# app.py — vulnbox (Restricted exec interface + flag interface + CORS)
from fastapi import FastAPI, Body, Header, HTTPException
from pydantic import BaseModel
import shlex, asyncio, os, re
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Echo Vulnbox (hardened exec)")

# Flag storage (simple example)
CURRENT_FLAG = ""

# Platform token: Set the PLATFORM_TOKEN environment variable in docker-compose.yml
PLATFORM_TOKEN = os.environ.get("PLATFORM_TOKEN", "please-set-a-secret")

# Allowed front-end origins (add your platform URL here)
ALLOW_ORIGINS = [
    "http://localhost:8000",  # Platform frontend
    "http://127.0.0.1:8000",
    "http://localhost:20081", # If you access from the same service origin
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Whitelisted programs (only these executables are allowed)
ALLOWED_PROG = {"curl", "nc", "ping", "tail", "cat", "ls", "grep"}

# Block dangerous characters (redirects, pipes, semicolons, backticks, $, etc.)
DANGEROUS_PATTERN = re.compile(r"[;&|`$<>]")

class ExecIn(BaseModel):
    cmd: str

@app.post("/set_flag")
def set_flag(value: str = Body(..., embed=True), x_platform_token: str | None = Header(None)):
    """
    Only the platform (checker/rotate_flags) can write the flag.
    Must provide x-platform-token that matches PLATFORM_TOKEN inside the container.
    """
    if x_platform_token != PLATFORM_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")
    global CURRENT_FLAG
    CURRENT_FLAG = value
    return {"ok": True}

@app.get("/flag")
def get_flag():
    # Publicly read the flag; if you want to restrict access, add token verification here
    return {"flag": CURRENT_FLAG}

@app.post("/exec")
async def exec_cmd(payload: ExecIn, x_team_token: str | None = Header(None)):
    """
    Restricted command execution endpoint — executes only whitelisted programs and strictly filters arguments.
    Authentication: the frontend is expected to include x_team_token in the header (platform provides blue team tokens).
    You should verify the x_team_token against your platform DB to ensure that it belongs to the blue team and only accesses its own vulnbox.
    (DB verification logic omitted in this example.)
    """
    cmd_str = payload.cmd.strip()
    if not cmd_str:
        raise HTTPException(status_code=400, detail="empty command")

    # Basic token check (for demonstration — recommended to query platform DB for token/team mapping)
    if not x_team_token:
        raise HTTPException(status_code=401, detail="missing x_team_token")

    # Block dangerous characters
    if DANGEROUS_PATTERN.search(cmd_str):
        raise HTTPException(status_code=400, detail="disallowed characters in command")

    # Split command and check if the program is in the whitelist
    try:
        parts = shlex.split(cmd_str)
    except Exception:
        raise HTTPException(status_code=400, detail="parse error")

    prog = parts[0]
    if prog not in ALLOWED_PROG:
        raise HTTPException(status_code=403, detail=f"program not allowed: {prog}")

    # Execute command (with timeout)
    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=12.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="command timeout")

        out = stdout.decode(errors="ignore")
        err = stderr.decode(errors="ignore")
        return {
            "rc": proc.returncode,
            "out": out,
            "err": err,
            "ts": __import__("datetime").datetime.utcnow().isoformat() + "Z"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"exec error: {e}")
