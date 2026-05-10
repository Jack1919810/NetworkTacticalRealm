# NetworkTacticalRealm

**A containerised Attack-Defense (AWD) CTF platform for cybersecurity education.**

NetworkTacticalRealm runs a live red-vs-blue exercise: blue teams defend their own vulnerable services ("vulnboxes"), red teams attack the others, flags rotate every tick, and an automated checker continuously scores both attack success and service availability (SLA).

> ⚠️ **Educational use only.** This platform deliberately exposes vulnerable services. Do not deploy on a public network without isolation.

---

## Why this is interesting (from a security engineering perspective)

- **Live flag rotation** — flags change every 120 s, so a one-shot exploit is not enough; attackers must operationalise their access
- **Role-based isolation** — red, blue, instructor, and admin tokens have separate capabilities; the API enforces that only red teams can submit flags, only admins can rotate or reset
- **Hardened red-team console** — `/red/exec` accepts only `curl` and `nc`, blocks shell metacharacters (`; & | \` $ > <`), and resolves targets through a DB-backed allow-list to prevent the platform itself from being weaponised
- **Platform-vulnbox trust boundary** — vulnboxes accept flag writes (`/set_flag`) only when `x-platform-token` matches the shared secret, preventing red teams from poisoning their targets' flag stores
- **Per-tick SLA scoring** — the checker writes a fresh flag, reads it back, and records `up`/`down` per service per tick; downtime costs the defender points
- **Multi-instance support** — `/games/{gid}/...` routes scope every state operation to a game instance, so multiple exercises can run on one platform

---

## Architecture

```
┌─────────────────┐       ┌────────────────────────────┐       ┌──────────────────┐
│  Red / Blue /   │       │      API (FastAPI)         │       │   PostgreSQL     │
│   Admin UIs     │ ◄───► │  ─────────────────────     │ ◄───► │  teams, flags,   │
│  (static HTML)  │       │  • /submit  • /scoreboard  │       │  submissions,    │
│                 │       │  • /red/exec • /admin/*    │       │  scores, ticks   │
└─────────────────┘       │  • APScheduler tick loop   │       └──────────────────┘
                          └────────────┬───────────────┘
                                       │ checker (HTTP + x-platform-token)
                                       ▼
                          ┌────────────────────────────┐
                          │   Vulnbox containers       │
                          │   (echo service, …)        │
                          │   /set_flag /flag /exec    │
                          └────────────────────────────┘
```

**Stack:** FastAPI, SQLAlchemy 2.0, PostgreSQL 16, APScheduler, httpx, Docker Compose.

---

## Quick start

```bash
# 1. Clone and configure secrets
git clone https://github.com/<your-handle>/NetworkTacticalRealm.git
cd NetworkTacticalRealm
cp .env.example .env

# 2. Generate strong tokens and put them in .env
python3 -c "import secrets; print('PLATFORM_TOKEN=' + secrets.token_hex(32))"
python3 -c "import secrets; print('ADMIN_TOKEN=' + secrets.token_hex(16))"
# (Edit .env with the values above and a strong POSTGRES_PASSWORD)

# 3. Build and start everything
docker compose up --build -d

# 4. Verify
curl http://localhost:8000/healthz        # → {"ok": true}
curl http://localhost:8000/scoreboard     # → seeded teams
```

The API logs the seeded team tokens on first start. Grab the red team token from the logs — you'll need it for `/submit`.

---

## A typical exploit round

1. **Find a flag.** The blue1 vulnbox exposes `GET /flag` on port `20081`:
   ```bash
   curl http://localhost:20081/flag
   # → {"flag":"FLAG{...}"}
   ```
2. **Submit it as red:**
   ```bash
   curl -X POST http://localhost:8000/submit \
        -H "X-Team-Token: <red_token>" \
        -H "Content-Type: application/json" \
        -d '"FLAG{...}"'
   # → {"status":"accepted",...}
   ```
3. **Watch the scoreboard** at `http://localhost:8000/scoreboard`. Attack points go up; defender's SLA points stay flat (or drop if their service is down when the checker runs).

---

## Project layout

```
NetworkTacticalRealm/
├── api/                       # Platform API
│   ├── main.py                # FastAPI app, routes, scheduler jobs
│   ├── models.py              # SQLAlchemy models (Team, Flag, Submission, Score, ...)
│   ├── db.py                  # Engine, session factory
│   ├── utils.py               # Flag generation, tick math
│   ├── checkers/              # Pluggable per-service SLA checkers
│   │   └── web_echo.py
│   ├── admin_ui/              # Admin panel (static HTML + JS)
│   ├── red_ui/                # Red team console
│   ├── blue_ui/               # Blue team dashboard
│   └── Dockerfile
├── vulnboxes/                 # Target services
│   └── echo/                  # Sample vulnbox: /set_flag /flag /exec
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Adding a new vulnerable service

1. Create `vulnboxes/<your_service>/` with a `Dockerfile` and the vulnerable app
2. Add a service block in `docker-compose.yml` (assign a unique port)
3. Implement a checker module under `api/checkers/<your_service>.py` that exports
   ```python
   async def check(team, service, db_flag: str) -> tuple[bool, str]:
       ...
   ```
4. Register the service via the seed function or directly in the database, pointing
   `checker_module` at `checkers.<your_service>:check`

---

## Roadmap

- [ ] Refactor `main.py` into clean-architecture layers (routes / services / repositories)
- [ ] Add more vulnbox templates (web SQLi, buffer overflow, deserialisation)
- [ ] First-blood bonuses and tiered scoring
- [ ] Per-team rate limiting on `/submit` and `/red/exec`
- [ ] Replay viewer for instructors (timeline of submissions and check results)

---

## License

MIT — see [LICENSE](./LICENSE).

## Disclaimer

Built for university coursework and self-directed learning. Run only inside an isolated network.
