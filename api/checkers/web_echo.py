# checkers/web_echo.py
import os, httpx

PLATFORM_TOKEN = os.environ.get("PLATFORM_TOKEN", "please-set-a-secret")

async def check(team, service, db_flag: str):
    base = f"http://{team.host}:{service.port}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1) First write the flag from DB into the vulnbox (must include x-platform-token)
        r = await client.post(
            f"{base}/set_flag",
            json={"value": db_flag},
            headers={"x-platform-token": PLATFORM_TOKEN},
        )
        if r.status_code != 200:
            return False, f"set_flag {r.status_code}"

        # 2) Then read back to verify
        # Flag read-back validation
        r = await client.get(f"{base}/flag")
        if r.status_code != 200:
            return False, "flag endpoint"
        try:
            got = r.json().get("flag")
        except Exception:
            got = r.text.strip()
        if got != db_flag:
            return False, "flag mismatch"

    return True, "ok"
