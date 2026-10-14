#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  auctionbidiq — VPS Deploy Guide
#  VPS: srv1334765.hstgr.cloud   (Docker + Traefik, same box as loadledger / insurancechekr)
#  URL: https://auctionbidiq.srv1334765.hstgr.cloud
#
#  Two containers on the shared n8n_default network: Postgres + app
#  (FastAPI serves both /api/* and the web UI). Traefik gives auto-SSL.
# ═══════════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 [VPS] — Clone the repo into /docker
# ─────────────────────────────────────────────────────────────────────────────
ssh root@srv1334765.hstgr.cloud
mkdir -p /docker && cd /docker
git clone https://github.com/speedore-tech/auctionbidiq.git auctionbidiq
cd auctionbidiq/deploy


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 [VPS] — Configure secrets
# ─────────────────────────────────────────────────────────────────────────────
cp auctionbidiq.env.example .env
nano .env
#   - DB_PASSWORD           → a strong password (letters/digits)
#   - SEED_ADMIN_EMAIL      → your login email
#   - SEED_ADMIN_PASSWORD   → your login password (8+ chars)
#   - DOMAIN can stay auctionbidiq.srv1334765.hstgr.cloud (add a DNS A record
#     pointing at the VPS if it isn't covered by a wildcard already).


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 [VPS] — Build & start (tables are created automatically on boot)
# ─────────────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml --env-file .env up -d --build

docker compose -f docker-compose.prod.yml ps
docker logs auctionbidiq-app --tail=50


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 [VPS] — Create the admin login (one time; re-run to reset the password)
# ─────────────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml exec auctionbidiq-app python scripts/create_admin.py
# Can't log in? List logins, then reset by typing the password at the prompt (bypasses .env):
docker compose -f docker-compose.prod.yml exec auctionbidiq-app python scripts/create_admin.py --list
docker compose -f docker-compose.prod.yml exec auctionbidiq-app python scripts/create_admin.py admin@speedore.com
# After that, add/reset/remove logins for partners on the Users page in the app.


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 [VPS] — Load your historical result sheets into the shared comp pool
# ─────────────────────────────────────────────────────────────────────────────
# From your PC first:  scp -r ./result_pdfs root@srv1334765.hstgr.cloud:/docker/auctionbidiq/inbox
docker cp /docker/auctionbidiq/inbox auctionbidiq-app:/app/inbox
docker compose -f docker-compose.prod.yml exec auctionbidiq-app python scripts/ingest_folder.py /app/inbox
# (or upload them one at a time on the Ingest page)
# Do NOT run scripts/seed_demo.py in production — it loads synthetic comps.


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Verify
# ─────────────────────────────────────────────────────────────────────────────
curl -I https://auctionbidiq.srv1334765.hstgr.cloud             # → HTTP 200 + SSL
curl -s https://auctionbidiq.srv1334765.hstgr.cloud/api/health  # → {"ok":true,...}
# Then open the URL and sign in with SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD.


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL — n8n auto-ingest (n8n runs on the same box)
# ─────────────────────────────────────────────────────────────────────────────
# Import n8n/ingest_results_workflow.json. In n8n set:
#   BIDIQ_URL   = http://auctionbidiq-app:8000   (internal, same n8n_default network)
#   BIDIQ_TOKEN = a bearer token from POST /api/auth/login → "token"


# ═══════════════════════════════════════════════════════════════════════════
# REDEPLOY — after pushing code changes
# ═══════════════════════════════════════════════════════════════════════════
cd /docker/auctionbidiq
git pull origin main
cd deploy
docker compose -f docker-compose.prod.yml --env-file .env up -d --build


# ═══════════════════════════════════════════════════════════════════════════
# TROUBLESHOOTING — "Container ... is restarting" / app won't start
# ═══════════════════════════════════════════════════════════════════════════
docker logs auctionbidiq-app --tail=40          # the real error is at the bottom
#  • "password authentication failed"  → DB_PASSWORD was changed after the first
#    start. Postgres keeps the original password in its volume. On a fresh install
#    with no data yet, reset it:
#      docker compose -f docker-compose.prod.yml down -v   # ⚠ deletes the DB volume
#      docker compose -f docker-compose.prod.yml --env-file .env up -d --build
#  • "failed to resolve host" → old build that put the password in a URL; git pull + rebuild.


# ═══════════════════════════════════════════════════════════════════════════
# USEFUL COMMANDS
# ═══════════════════════════════════════════════════════════════════════════
docker logs auctionbidiq-app -f --tail=100
docker compose -f docker-compose.prod.yml ps
# DB shell:
docker compose -f docker-compose.prod.yml exec auctionbidiq-db psql -U auctionbidiq
# Backup the database:
docker compose -f docker-compose.prod.yml exec auctionbidiq-db \
  pg_dump -U auctionbidiq auctionbidiq > ~/auctionbidiq-$(date +%F).sql
