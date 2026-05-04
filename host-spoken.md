# Hosting FluentMeet Backend on DigitalOcean

> **Target:** `spoken.unraveldocs.xyz` → DigitalOcean Droplet  
> **Stack:** FastAPI + Supabase (PostgreSQL) + Redis + Kafka (Dockerized) + Nginx reverse proxy + Let's Encrypt SSL

---

## Table of Contents

1. [Project Files to Create](#1-project-files-to-create)
2. [Supabase Database Setup](#2-supabase-database-setup)
3. [DigitalOcean Droplet Setup](#3-digitalocean-droplet-setup)
4. [Namecheap DNS Configuration](#4-namecheap-dns-configuration)
5. [Server Initial Setup](#5-server-initial-setup)
6. [Clone & Configure the Project](#6-clone--configure-the-project)
7. [Build & Launch with Docker Compose](#7-build--launch-with-docker-compose)
8. [Install & Configure Nginx](#8-install--configure-nginx)
9. [SSL with Let's Encrypt](#9-ssl-with-lets-encrypt)
10. [Run Database Migrations](#10-run-database-migrations)
11. [Verify Deployment](#11-verify-deployment)
12. [Maintenance & Troubleshooting](#12-maintenance--troubleshooting)

---

## 1. Project Files to Create

Before deploying, add these files to the repo root.

### 1a. `Dockerfile` (Multi-Stage)

```dockerfile
# ============================================================
# Stage 1 — Builder: install dependencies in a clean layer
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile psycopg2, cryptography, etc.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage 2 — Runtime: lean production image
# ============================================================
FROM python:3.11-slim AS runtime

# Only the PostgreSQL client lib is needed at runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY pyproject.toml ./
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/
COPY templates/ ./templates/

# Own files by appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with uvicorn — 4 workers for production
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### 1b. `.dockerignore`

```
.venv
.git
.github
.mypy_cache
.pytest_cache
.ruff_cache
.coverage
htmlcov
__pycache__
*.pyc
.env
.idea
.vscode
tests/
docs/
scripts/
issues/
*.raw
```

### 1c. `docker-compose.prod.yml`

> **Note:** PostgreSQL is hosted on **Supabase** (external), so there is no `postgres` service here.

```yaml
version: "3.8"

services:
  # ── Redis ──────────────────────────────────────────────────
  redis:
    image: redis:7-alpine
    container_name: fluentmeet-redis
    restart: always
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - fluentmeet-net

  # ── Kafka (KRaft mode) ────────────────────────────────────
  kafka:
    image: apache/kafka:3.7.0
    container_name: fluentmeet-kafka
    restart: always
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:29092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_INTER_BROKER_LISTENER_NAME: PLAINTEXT
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      CLUSTER_ID: MkU3OEVBNTcwNTJENDM2Qk
    volumes:
      - kafka_data:/var/lib/kafka/data
    healthcheck:
      test: ["CMD-SHELL", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:29092"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s
    networks:
      - fluentmeet-net

  # ── FluentMeet API ─────────────────────────────────────────
  api:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: fluentmeet-api
    restart: always
    env_file:
      - .env.prod
    environment:
      REDIS_HOST: redis
      KAFKA_BOOTSTRAP_SERVERS: kafka:29092
      # DATABASE_URL is read from .env.prod (Supabase pooled connection)
    depends_on:
      redis:
        condition: service_healthy
      kafka:
        condition: service_healthy
    ports:
      - "8000:8000"
    networks:
      - fluentmeet-net

volumes:
  redis_data:
  kafka_data:

networks:
  fluentmeet-net:
    driver: bridge
```

### 1d. `.env.prod.example`

```env
# ── General ──────────────────────────────────────────────────
PROJECT_NAME=FluentMeet
VERSION=1.15.0
API_V1_STR=/api/v1

# ── Security ─────────────────────────────────────────────────
SECRET_KEY=<generate-a-64-char-random-string>
ADMIN_EMAIL=admin@unraveldocs.xyz
ADMIN_PASSWORD=<strong-admin-password>

# ── Database (Supabase) ──────────────────────────────────────
# Pooled connection (PgBouncer port 6543) — used by the app at runtime
DATABASE_URL=postgresql+psycopg2://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
# Direct connection (port 5432) — used ONLY for Alembic migrations
DATABASE_URL_DIRECT=postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

# ── Redis ────────────────────────────────────────────────────
REDIS_HOST=redis
REDIS_PORT=6379

# ── Kafka ────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS=kafka:29092

# ── External Services ───────────────────────────────────────
DEEPGRAM_API_KEY=
DEEPL_API_KEY=
VOICE_AI_API_KEY=
OPENAI_API_KEY=

# ── Google OAuth ─────────────────────────────────────────────
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://spoken.unraveldocs.xyz/api/v1/auth/google/callback

# ── Cloudinary ───────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=

# ── Email ────────────────────────────────────────────────────
MAILGUN_API_KEY=
MAILGUN_DOMAIN=
MAILGUN_FROM_ADDRESS=no-reply@unraveldocs.xyz
RESEND_API_KEY=

# ── Frontend ─────────────────────────────────────────────────
FRONTEND_BASE_URL=https://your-frontend-url.com
```

**Commit and push** all four files (`Dockerfile`, `.dockerignore`, `docker-compose.prod.yml`, `.env.prod.example`) to your repo before starting the server setup.

---

## 2. Supabase Database Setup

Instead of running PostgreSQL in Docker (which eats ~200–400 MB RAM), we use **Supabase** as a managed Postgres host.

### 2a. Create a Supabase Project

1. Go to [supabase.com](https://supabase.com) and sign up / log in
2. Click **New Project**
3. Configure:

| Setting          | Value                                |
|------------------|--------------------------------------|
| **Organization** | Your org (or create one)             |
| **Project Name** | `fluentmeet-prod`                    |
| **Database Password** | Generate a strong password (save it!) |
| **Region**       | Same region as your Droplet          |
| **Plan**         | Free tier works to start             |

4. Click **Create new project** and wait ~2 minutes for provisioning

### 2b. Get Your Connection Strings

1. Go to **Project Settings → Database**
2. Scroll to **Connection string** section
3. You need **two** connection strings:

| Purpose | Port | Mode | Where to find |
|---------|------|------|---------------|
| **App runtime** (pooled) | `6543` | Transaction via PgBouncer | Connection string → **Transaction** tab |
| **Migrations** (direct) | `5432` | Direct / Session | Connection string → **Session** tab |

4. The strings look like:

```
# Pooled (for .env.prod DATABASE_URL) — use port 6543
postgresql+psycopg2://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:6543/postgres

# Direct (for Alembic migrations DATABASE_URL_DIRECT) — use port 5432
postgresql+asyncpg://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
```

> **Why two URLs?** PgBouncer (port 6543) is great for app connections but doesn't support DDL statements (`ALTER TABLE`, `CREATE INDEX`, etc.) that Alembic runs during migrations. Use port 5432 for migrations only.

### 2c. Verify Connection (Optional)

You can test from your local machine if you have `psql` installed:

```bash
psql "postgresql://postgres.<ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:6543/postgres"
```

---

## 3. DigitalOcean Droplet Setup

### 2a. Create a Droplet

1. Log in to [cloud.digitalocean.com](https://cloud.digitalocean.com)
2. Click **Create → Droplets**
3. Configure:

| Setting          | Recommended Value                              |
|------------------|-------------------------------------------------|
| **Region**       | Closest to your users (e.g. `LON1`, `NYC1`)     |
| **Image**        | **Ubuntu 24.04 LTS**                            |
| **Size**         | **Basic → Regular** → **$12/mo** (2 GB / 1 vCPU). Since PostgreSQL is on Supabase, 2 GB is sufficient for Redis + Kafka + API |
| **Auth**         | **SSH Key** (strongly recommended over password) |
| **Hostname**     | `fluentmeet-prod`                               |

> 💡 With Postgres offloaded to Supabase, you save ~400 MB RAM. A **2 GB** Droplet should work. If Kafka gets OOM-killed under heavy load, upgrade to 4 GB.

4. Click **Create Droplet** and note the **public IPv4 address** (e.g. `164.90.xxx.xxx`)

### 3b. Add SSH Key (if you haven't)

On your **local Windows machine**:

```powershell
# Generate key if you don't have one
ssh-keygen -t ed25519 -C "your-email@example.com"

# Copy public key to clipboard
Get-Content ~/.ssh/id_ed25519.pub | Set-Clipboard
```

Paste this into DigitalOcean → **Settings → Security → SSH Keys → Add SSH Key**.

---

## 4. Namecheap DNS Configuration

You need to point `spoken.unraveldocs.xyz` to your Droplet's IP.

### Option A: Use Namecheap DNS (Simpler)

1. Log in to [namecheap.com](https://namecheap.com) → **Domain List** → click **Manage** next to `unraveldocs.xyz`
2. Go to **Advanced DNS** tab
3. Add a new **A Record**:

| Type       | Host      | Value              | TTL       |
|------------|-----------|--------------------|-----------|
| A Record   | `spoken`  | `164.90.xxx.xxx`   | Automatic |

4. Save. DNS propagation takes 5–30 minutes.

### Option B: Use DigitalOcean DNS (Optional, Better for DO integration)

1. In Namecheap → **Domain List** → **Manage** → **Nameservers** → set to **Custom DNS**:
   ```
   ns1.digitalocean.com
   ns2.digitalocean.com
   ns3.digitalocean.com
   ```
2. In DigitalOcean → **Networking → Domains** → Add `unraveldocs.xyz`
3. Create an **A record**: hostname = `spoken`, value = your Droplet IP

> **Recommendation:** Option A is simplest if you just need a subdomain. Option B is better if you plan to manage everything from DO.

### Verify DNS

```bash
# Run this after ~15 minutes
nslookup spoken.unraveldocs.xyz
# Should return your Droplet's IP
```

---

## 5. Server Initial Setup

SSH into your Droplet from your Windows terminal:

```powershell
ssh root@164.90.xxx.xxx
```

### 4a. System Updates & Firewall

```bash
# Update packages
apt update && apt upgrade -y

# Set up UFW firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
ufw status
```

### 4b. Create a Deploy User (Security Best Practice)

```bash
adduser deploy
usermod -aG sudo deploy

# Copy SSH key to new user
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

# Test login in a NEW terminal before closing root session
# ssh deploy@164.90.xxx.xxx
```

From now on, SSH as `deploy`:

```powershell
ssh deploy@164.90.xxx.xxx
```

### 4c. Install Docker & Docker Compose

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh

# Add deploy user to docker group (no sudo needed for docker)
sudo usermod -aG docker deploy

# Log out and back in for group to take effect
exit
```

SSH back in:

```powershell
ssh deploy@164.90.xxx.xxx
```

Verify:

```bash
docker --version
docker compose version
```

### 4d. Install Nginx

```bash
sudo apt install -y nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

---

## 6. Clone & Configure the Project

### 5a. Clone the Repository

```bash
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/Brints/FluentMeet.git fluentmeet
cd fluentmeet
```

> If the repo is private, set up a [GitHub Deploy Key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys) or use a Personal Access Token.

### 5b. Create the Production `.env.prod`

```bash
cp .env.prod.example .env.prod
nano .env.prod
```

**Fill in all the real values.** Paste your Supabase connection strings for `DATABASE_URL` and `DATABASE_URL_DIRECT`.

Generate a strong `SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

```

---

## 7. Build & Launch with Docker Compose

### 7a. Build the Images

```bash
cd ~/apps/fluentmeet
docker compose -f docker-compose.prod.yml build --no-cache
```

### 7b. Start All Services

```bash
docker compose -f docker-compose.prod.yml up -d
```

### 7c. Verify Services are Running

```bash
docker compose -f docker-compose.prod.yml ps
```

Expected output — all containers should show `Up (healthy)`:

```
NAME                  STATUS
fluentmeet-redis      Up (healthy)
fluentmeet-kafka      Up (healthy)
fluentmeet-api        Up (healthy)
```

### 7d. Check API Logs

```bash
docker compose -f docker-compose.prod.yml logs -f api
```

Test locally on the server:

```bash
curl http://localhost:8000/health
```

---

## 8. Install & Configure Nginx

Nginx acts as a reverse proxy: it receives HTTPS traffic on port 443, terminates SSL, and forwards requests to the Docker container on port 8000. It also handles WebSocket upgrades for your meeting connections.

### 8a. Create Nginx Config

```bash
sudo nano /etc/nginx/sites-available/spoken.unraveldocs.xyz
```

Paste this config:

```nginx
# ── Upstream ─────────────────────────────────────────────────
upstream fluentmeet_api {
    server 127.0.0.1:8000;
}

# ── HTTP → HTTPS redirect (will be active after SSL setup) ──
server {
    listen 80;
    listen [::]:80;
    server_name spoken.unraveldocs.xyz;

    # Let's Encrypt challenge directory
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# ── Main HTTPS Server (enable after SSL cert is obtained) ───
# Uncomment this block AFTER running certbot in Step 8
#
# server {
#     listen 443 ssl http2;
#     listen [::]:443 ssl http2;
#     server_name spoken.unraveldocs.xyz;
#
#     ssl_certificate     /etc/letsencrypt/live/spoken.unraveldocs.xyz/fullchain.pem;
#     ssl_certificate_key /etc/letsencrypt/live/spoken.unraveldocs.xyz/privkey.pem;
#     ssl_protocols       TLSv1.2 TLSv1.3;
#     ssl_ciphers         HIGH:!aNULL:!MD5;
#     ssl_prefer_server_ciphers on;
#
#     # Security headers
#     add_header X-Frame-Options SAMEORIGIN always;
#     add_header X-Content-Type-Options nosniff always;
#     add_header X-XSS-Protection "1; mode=block" always;
#     add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
#
#     # Max upload size
#     client_max_body_size 50M;
#
#     # ── API & general proxy ────────────────────────────────
#     location / {
#         proxy_pass http://fluentmeet_api;
#         proxy_set_header Host $host;
#         proxy_set_header X-Real-IP $remote_addr;
#         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto $scheme;
#
#         # Timeouts
#         proxy_connect_timeout 60s;
#         proxy_send_timeout    60s;
#         proxy_read_timeout    60s;
#     }
#
#     # ── WebSocket endpoints ────────────────────────────────
#     location /api/v1/meetings/ {
#         proxy_pass http://fluentmeet_api;
#         proxy_http_version 1.1;
#         proxy_set_header Upgrade $http_upgrade;
#         proxy_set_header Connection "upgrade";
#         proxy_set_header Host $host;
#         proxy_set_header X-Real-IP $remote_addr;
#         proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
#         proxy_set_header X-Forwarded-Proto $scheme;
#
#         # WebSocket timeout — keep alive for long meetings
#         proxy_read_timeout 3600s;
#         proxy_send_timeout 3600s;
#     }
# }
```

### 8b. Enable the Site

```bash
# Create symlink
sudo ln -s /etc/nginx/sites-available/spoken.unraveldocs.xyz /etc/nginx/sites-enabled/

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Create certbot webroot
sudo mkdir -p /var/www/certbot

# Test config
sudo nginx -t

# Reload
sudo systemctl reload nginx
```

---

## 9. SSL with Let's Encrypt

### 9a. Install Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
```

### 9b. Obtain Certificate

```bash
sudo certbot certonly --webroot \
    -w /var/www/certbot \
    -d spoken.unraveldocs.xyz \
    --email your-email@example.com \
    --agree-tos \
    --non-interactive
```

### 9c. Enable the HTTPS Block in Nginx

```bash
sudo nano /etc/nginx/sites-available/spoken.unraveldocs.xyz
```

**Uncomment the entire `server { listen 443 ... }` block** (remove every `#` and the leading space from each line in that block).

Then:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

### 9d. Auto-Renewal

Certbot installs a systemd timer automatically. Verify:

```bash
sudo systemctl status certbot.timer
```

Test renewal:

```bash
sudo certbot renew --dry-run
```

---

## 10. Run Database Migrations

Since Alembic needs to run DDL statements, use the **direct** Supabase connection (port 5432), not the pooled one.

### 10a. Run Alembic Inside the API Container

The `alembic.ini` has a hardcoded `sqlalchemy.url` for local dev. Override it with your Supabase direct URL:

```bash
docker compose -f docker-compose.prod.yml exec api \
    alembic -c alembic.ini -x sqlalchemy.url="${DATABASE_URL_DIRECT}" upgrade head
```

Or enter the container and override manually:

```bash
docker compose -f docker-compose.prod.yml exec api bash
# Inside the container — replace with your actual Supabase direct connection string:
sed -i "s|sqlalchemy.url = .*|sqlalchemy.url = postgresql+asyncpg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres|" alembic.ini
alembic upgrade head
exit
```

> ⚠️ Always use port **5432** (direct) for migrations. PgBouncer (6543) will fail on `ALTER TABLE` / `CREATE TABLE` statements.

---

## 11. Verify Deployment

### 10a. Health Check

```bash
curl https://spoken.unraveldocs.xyz/health
```

Expected response:

```json
{
    "status": "ok",
    "version": "1.15.0",
    "services": {
        "kafka": {"status": "healthy"}
    }
}
```

### 10b. API Docs

Open in your browser:

```
https://spoken.unraveldocs.xyz/docs
```

You should see the FastAPI Swagger UI.

### 10c. WebSocket Test

```bash
# Install wscat if needed: npm install -g wscat
wscat -c wss://spoken.unraveldocs.xyz/api/v1/meetings/ws/test
```

### 10d. SSL Verification

```bash
curl -vI https://spoken.unraveldocs.xyz 2>&1 | grep -E "SSL|subject|expire"
```

---

## 12. Maintenance & Troubleshooting

### Updating the Application

```bash
cd ~/apps/fluentmeet
git pull origin main

# Rebuild and restart only the API container
docker compose -f docker-compose.prod.yml build api --no-cache
docker compose -f docker-compose.prod.yml up -d api

# Run any new migrations
docker compose -f docker-compose.prod.yml exec api alembic upgrade head
```

### Viewing Logs

```bash
# All services
docker compose -f docker-compose.prod.yml logs -f

# Specific service
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f kafka

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Restarting Services

```bash
# Restart everything
docker compose -f docker-compose.prod.yml restart

# Restart single service
docker compose -f docker-compose.prod.yml restart api
```

### Database Backup (Supabase)

Supabase provides **automatic daily backups** on paid plans. For manual backups, install `pg_dump` on your server:

```bash
# Install PostgreSQL client tools
sudo apt install -y postgresql-client

# Create backup directory
mkdir -p ~/backups

# Manual backup via Supabase direct connection
PGPASSWORD="<your-db-password>" pg_dump \
    -h aws-0-<region>.pooler.supabase.com \
    -p 5432 \
    -U postgres.<ref> \
    -d postgres \
    > ~/backups/fluentmeet_$(date +%Y%m%d_%H%M%S).sql

# Set up daily cron
crontab -e
# Add:
# 0 3 * * * PGPASSWORD="<password>" pg_dump -h aws-0-<region>.pooler.supabase.com -p 5432 -U postgres.<ref> -d postgres > /home/deploy/backups/fluentmeet_$(date +\%Y\%m\%d).sql
```

### Common Issues

| Problem | Solution |
|---------|----------|
| `502 Bad Gateway` | API container isn't running. Check `docker compose logs api` |
| `Connection refused` on port 8000 | Ensure `ports: "8000:8000"` is in compose and container is healthy |
| Kafka OOM killed | Upgrade Droplet to 4 GB RAM |
| SSL cert expired | Run `sudo certbot renew && sudo systemctl reload nginx` |
| WebSocket drops | Check `proxy_read_timeout` in Nginx (should be `3600s`) |
| DNS not resolving | Wait 30 min, verify A record in Namecheap, try `dig spoken.unraveldocs.xyz` |
| `alembic` migration fails | Use port **5432** (direct), not 6543 (pooled). PgBouncer doesn't support DDL |
| Supabase connection timeout | Ensure Droplet region matches Supabase region. Check Supabase dashboard for paused projects (free tier pauses after 1 week of inactivity) |

### Monitoring Droplet Resources

```bash
# Real-time resource usage
htop

# Docker resource usage
docker stats

# Disk usage
df -h
```

---

## Quick Reference — Command Cheat Sheet

```bash
# SSH into server
ssh deploy@164.90.xxx.xxx

# Navigate to project
cd ~/apps/fluentmeet

# Full deploy (pull + rebuild + migrate)
git pull origin main && \
docker compose -f docker-compose.prod.yml build api --no-cache && \
docker compose -f docker-compose.prod.yml up -d api && \
docker compose -f docker-compose.prod.yml exec api alembic upgrade head

# Tear down everything (preserves volumes)
docker compose -f docker-compose.prod.yml down

# Tear down AND delete data (⚠️ destructive)
docker compose -f docker-compose.prod.yml down -v

# Check everything is healthy
docker compose -f docker-compose.prod.yml ps
curl https://spoken.unraveldocs.xyz/health
```
