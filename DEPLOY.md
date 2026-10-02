# Deploy ReelKut to AWS EC2 (learning guide)

Canonical production URL: **https://reelkut.com**  
Stack: Ubuntu EC2 → Docker (API) → Nginx (SPA + reverse proxy) → Let's Encrypt → Clerk Production

---

## Step 0 — What you need ready

- [ ] EC2 instance running (Ubuntu 22.04/24.04 recommended)
- [ ] SSH key pair downloaded (`.pem`)
- [ ] Domain **reelkut.com** you can edit DNS for
- [ ] GitHub access to this repo
- [ ] Clerk account (we will create a **Production** app)
- [ ] A **separate** Google API key for production (budget alert on)

---

## Step 1 — EC2 security group + Elastic IP

In **AWS Console → EC2**:

1. Select your instance → **Security** tab → security group → **Edit inbound rules**:

| Type | Port | Source |
|------|------|--------|
| SSH | 22 | My IP only |
| HTTP | 80 | 0.0.0.0/0 |
| HTTPS | 443 | 0.0.0.0/0 |

2. **Elastic IPs → Allocate → Associate** with this instance.  
   Write down the public IP: `________________`

3. Note:
   - Instance type (prefer **t3.large** if Whisper feels slow)
   - Region
   - Username: usually `ubuntu` for Ubuntu AMIs

---

## Step 2 — SSH in

On your Mac (adjust path/IP):

```bash
chmod 400 ~/Downloads/your-key.pem
ssh -i ~/Downloads/your-key.pem ubuntu@YOUR_ELASTIC_IP
```

If that works, you’re on the box. Reply in chat: **“Step 2 done”** and paste the Elastic IP (safe to share).

---

## Step 3 — DNS (do while on the box or in parallel)

At your domain registrar for **reelkut.com**:

| Type | Name | Value |
|------|------|--------|
| A | `@` (apex) | Elastic IP |
| A or CNAME | `www` | same IP, or CNAME → `reelkut.com` |

Wait until `dig +short reelkut.com` returns the Elastic IP (can take a few minutes).

We will use **apex** as canonical (`https://reelkut.com`) and redirect `www` → apex.

---

## Step 4 — Install Docker, Nginx, Certbot (on EC2)

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git nginx

# Docker (official convenience script is fine for learning)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
# log out and ssh back in so docker works without sudo
exit
```

SSH back in, then:

```bash
docker --version
docker compose version
```

Optional data disk (if you attached a second EBS):

```bash
sudo mkdir -p /data/projects
sudo chown -R ubuntu:ubuntu /data
```

Otherwise use `/opt/reelkut/projects` (created in Step 5).

---

## Step 5 — Clone repo + env files

```bash
sudo mkdir -p /opt/reelkut
sudo chown ubuntu:ubuntu /opt/reelkut
cd /opt/reelkut
git clone https://github.com/chaiitanyaanaik/ai-reel-factory.git .
mkdir -p projects
```

Create **API secrets** (never commit):

```bash
cp .env.example .env
nano .env   # or vim
```

Set at least:

```bash
ENVIRONMENT=production
AUTH_MODE=clerk
AUTH_REQUIRED=1
AUTH_SECRET=   # run: openssl rand -hex 32
COOKIE_SECURE=1
CORS_ORIGINS=https://reelkut.com,https://www.reelkut.com
CLERK_ISSUER=https://REPLACE_ME.clerk.accounts.dev   # Production Frontend API URL
GOOGLE_API_KEY=your_prod_key
BROLL_VEO_ONLY=1
BROLL_USE_VEO=1
VEO_PARALLELISM=2
RATE_LIMIT_PROJECTS_PER_DAY=20
RATE_LIMIT_JOBS_PER_DAY=30
RATE_LIMIT_BROLL_EDITS_PER_DAY=40
```

```bash
chmod 600 .env
```

---

## Step 6 — Clerk Production (on your laptop / browser)

1. [Clerk Dashboard](https://dashboard.clerk.com) → create or open app → switch to **Production**.
2. **Configure → Domains** (or Paths): allow `https://reelkut.com` and `https://www.reelkut.com`.
3. **API Keys**:
   - Publishable key `pk_live_…` → used when building the frontend
   - **Frontend API URL** → paste into EC2 `.env` as `CLERK_ISSUER` (no trailing slash)
4. Sign-in / sign-up redirect URLs: `https://reelkut.com`, `https://reelkut.com/*`

Do **not** put `sk_live` in the frontend. Backend only needs issuer + JWT verification (JWKS).

---

## Step 7 — Start API container

```bash
cd /opt/reelkut
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
curl -s http://127.0.0.1:8000/health
```

Expect JSON with `"status":"ok"` and `"auth_mode":"clerk"`.

Logs:

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=100
```

---

## Step 8 — Build frontend (on EC2)

```bash
cd /opt/reelkut/frontend
# Node 20 via nvm or nodesource — example with NodeSource:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

echo 'VITE_CLERK_PUBLISHABLE_KEY=pk_live_YOUR_KEY' > .env.production.local
chmod 600 .env.production.local
npm ci
npm run build
# output: frontend/dist
```

---

## Step 9 — Nginx + TLS

```bash
sudo cp /opt/reelkut/deploy/nginx.reelkut.conf /etc/nginx/sites-available/reelkut
sudo ln -sf /etc/nginx/sites-available/reelkut /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Certbot (after DNS points at this IP):

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d reelkut.com -d www.reelkut.com
```

Follow prompts; choose redirect HTTP → HTTPS.

---

## Step 10 — Smoke test

1. Open https://reelkut.com  
2. Sign in with Clerk (Production)  
3. Create a cut, upload a short clip, run plan (expensive: Veo — use a tiny clip first)  
4. `GET https://reelkut.com/health`  

If auth fails: issuer mismatch (`pk_live` vs `CLERK_ISSUER` from **same** Production instance).

---

## Updating later

```bash
cd /opt/reelkut
git pull
docker compose -f docker-compose.prod.yml up -d --build
cd frontend && npm ci && npm run build
sudo systemctl reload nginx
```

---

## Cost / safety notes

- Whisper + Veo = CPU + Google spend. Keep rate limits on.
- Separate Google key for prod; set a billing budget alert.
- Security group: SSH = your IP only.
- `.env` and `frontend/.env.production.local` stay on the server only.
