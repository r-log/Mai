# Mai Phase A Deployment Runbook

> **Execution context:** This runbook is executed by the owner against real infrastructure (hosting, Neon Postgres, Cloudflare account). The CLI commands referenced (`init-db`, `registry-load`, `refresh`, `serve`) are pre-tested and ready to invoke. All Python dependencies are specified in `pyproject.toml`.

## 1. Provision the box

Deploy a small always-on Linux host (VPS or Fly.io machine) with a persistent disk for `mirrors/` directory. This host will run the refresh cron service continuously.

### 1.1 System setup

1. SSH into the new host.
2. Ensure the host is running **Linux** (Ubuntu 22.04 LTS recommended).
3. Create a persistent mount point for git mirrors:
   ```bash
   sudo mkdir -p /srv/mai/mirrors
   sudo chown $USER:$USER /srv/mai/mirrors
   chmod 755 /srv/mai/mirrors
   ```

### 1.2 Install dependencies

```bash
# Update system packages
sudo apt-get update
sudo apt-get upgrade -y

# Install Git and Python 3.12
sudo apt-get install -y git python3.12 python3.12-venv python3-pip

# Verify versions
git --version    # Should be >= 2.30
python3.12 --version  # Should be 3.12.x
```

### 1.3 Clone the Mai project and install

```bash
# Clone the repository (use `origin` with appropriate credentials)
cd /srv/mai
git clone <mai-repository-url> app
cd app

# Create and activate a Python virtual environment
python3.12 -m venv venv
source venv/bin/activate

# Upgrade pip and install the project with dependencies
pip install --upgrade pip setuptools wheel
pip install -e .

# Verify the `mai` command is available
mai --help
```

---

## 2. Database (Neon Postgres)

### 2.1 Create a Neon project

1. Log in to [Neon Console](https://console.neon.tech).
2. Create a new **Project** for the Mai instance.
3. Note the generated database credentials:
   - **Host:** `<project>.neon.tech`
   - **User:** `neondb_owner` (or similar)
   - **Password:** (auto-generated; save securely)
   - **Database:** `neondb`
   - **Connection string format:** `postgresql+asyncpg://<user>:<password>@<host>/<dbname>`

### 2.2 Set the DATABASE_URL in `.env`

On the deployment box, edit the `.env` file:

```bash
cd /srv/mai/app
nano .env
```

Add or update:
```
DATABASE_URL=postgresql+asyncpg://neondb_owner:<password>@<project>.neon.tech/neondb
```

**Important:** Ensure `asyncpg` is installed; it is included in `pyproject.toml` and was installed with `pip install -e .` above.

### 2.3 Initialize the database schema

```bash
cd /srv/mai/app
source venv/bin/activate

python -m mai.cli init-db
```

Expected output:
```
db initialized
```

This creates all tables (Commit, Repo, PortCandidate, etc.) in the Neon database.

---

## 3. Secrets in `.env`

Edit `/srv/mai/app/.env` to include all required secrets. This file is git-ignored and must never be committed.

```bash
# Database (from Neon, step 2.2 above)
DATABASE_URL=postgresql+asyncpg://neondb_owner:<password>@<project>.neon.tech/neondb

# GitHub API token (for harvesting commits and branches)
GITHUB_TOKEN=ghp_<your-token>

# OpenRouter API key (for enrichment with LLM)
OPENROUTER_API_KEY=sk-or-v1-<your-key>

# Firecrawl API key (for IPS bug-tracker crawling)
FIRECRAWL_API_KEY=<your-firecrawl-key>

# Deploy hook (script invoked after each refresh cycle)
DEPLOY_COMMAND="bash /srv/mai/app/scripts/deploy_site.sh"

# Refresh interval (default 10800 = 3 hours; in seconds)
REFRESH_INTERVAL_SECONDS=3600
```

### 3.1 Permissions

Ensure `.env` is readable only by the service user:

```bash
chmod 600 /srv/mai/app/.env
ls -la .env  # Should show -rw------- (mode 600)
```

---

## 4. Seed data

### 4.1 Load repositories from a registry

The `registry-load` command reads a markdown README (listing target repositories) and populates the database.

Prepare a README with repository entries. Example format:

```markdown
# Watched Repositories

## MaNGOS Three
- **Repo:** mangosthree/server
- **Core:** Cata
- **URL:** https://github.com/mangosthree/server

## MaNGOS Two
- **Repo:** mangostwo/server
- **Core:** WotLK
- **URL:** https://github.com/mangostwo/server

## Trinity Core (4.3.4)
- **Repo:** TrinityCore/TrinityCore
- **Core:** Cata (4.3.4)
- **URL:** https://github.com/TrinityCore/TrinityCore
```

Then load it:

```bash
cd /srv/mai/app
source venv/bin/activate

python -m mai.cli registry-load <path-to-README.md>
```

Expected output:
```
loaded 3 repos
```

### 4.2 Run the first refresh cycle

This populates the git mirrors, analyzes port candidates, and generates the static site:

```bash
cd /srv/mai/app
source venv/bin/activate

python -m mai.cli refresh
```

Expected output:
```
refresh: +<N> commits, <M> repos harvested, <K> port candidates, <P> pages
```

This command:
- Clones/updates all repositories into `./mirrors/` (or the path in `GIT_MIRROR_DIR`).
- Harvests all commits from GitHub API (requires `GITHUB_TOKEN`).
- Crawls the IPS bug tracker (requires `FIRECRAWL_API_KEY`).
- Computes port candidates and subsystem classification.
- Generates static HTML into `mai-data/`.
- Invokes the deploy command (if `DEPLOY_COMMAND` is set).

Monitor the logs carefully for any errors. If seeds fail, review `.env` secrets are complete.

---

## 5. Deploy command

### 5.1 Create the deploy script

Create the file `/srv/mai/app/scripts/deploy_site.sh`:

```bash
#!/bin/bash
set -euo pipefail

# Deploy script: called by refresh cycle to publish static site updates.
# Requires: hugo, wrangler (Cloudflare Pages CLI)
# Environment: PROJECT_NAME=mai (or customize below)

PROJECT_NAME="mai"
LEDGER_PATH="/srv/mai/app/mai-data"

echo "[deploy] Building Hugo site from $LEDGER_PATH..."
hugo -s "$LEDGER_PATH" --minify

echo "[deploy] Deploying to Cloudflare Pages project: $PROJECT_NAME"
wrangler pages deploy "$LEDGER_PATH/public" --project-name "$PROJECT_NAME"

echo "[deploy] Site deployed successfully."
```

Make it executable:

```bash
chmod +x /srv/mai/app/scripts/deploy_site.sh
```

### 5.2 Install deployment tools

Install Hugo and Wrangler CLI:

```bash
# Hugo (static site generator)
sudo apt-get install -y hugo

# Node.js and npm (for Wrangler)
sudo apt-get install -y nodejs npm

# Wrangler (Cloudflare Pages CLI)
npm install -g wrangler

# Verify
hugo version
wrangler --version
```

### 5.3 Configure Wrangler for Cloudflare Pages

Create or update `wrangler.toml` in the project root:

```toml
name = "mai"
type = "javascript"
account_id = "<your-cloudflare-account-id>"

[[env.production]]
routes = [
  { pattern = "mai.example.com", zone_name = "example.com" }
]
```

Authenticate Wrangler with your Cloudflare account:

```bash
wrangler login
```

### 5.4 Alternative: Cloudflare Pages git-integration

Instead of invoking a shell script, you can use **Cloudflare Pages' native git integration**:

1. Push the regenerated site data to a GitHub branch or repository.
2. Connect that repository to Cloudflare Pages in the dashboard.
3. Configure Pages to build via Hugo and deploy on each push.

In this case, set `DEPLOY_COMMAND` to a script that commits and pushes:

```bash
#!/bin/bash
set -euo pipefail

LEDGER_PATH="/srv/mai/app/mai-data"
GIT_REPO="https://github.com/<owner>/<mai-data-repo>.git"

echo "[deploy] Committing generated site data..."
cd "$LEDGER_PATH"
git add -A
git commit -m "chore: auto-generated site data $(date -u +%Y-%m-%dT%H:%M:%SZ)" || echo "No changes to commit"

echo "[deploy] Pushing to GitHub..."
git push origin main

echo "[deploy] Cloudflare Pages build triggered."
```

---

## 6. Cloudflare Pages + Access

### 6.1 Create the Cloudflare Pages project

1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com).
2. Navigate to **Pages** > **Create a project**.
3. Choose **Direct upload** (if not using git integration from section 5.4).
4. Name the project: `mai`.
5. Upload the contents of `mai-data/public/` (or leave empty; Wrangler deploys it).
6. Note the deployed URL: `https://mai.<your-cloudflare-pages-domain>`.

### 6.2 Add a custom domain (optional)

If you own a domain:

1. In the Pages project settings, navigate to **Custom domains**.
2. Add your domain (e.g., `mai.example.com`).
3. Follow the DNS setup instructions (usually add a CNAME record).

### 6.3 Protect with Cloudflare Access

Cloudflare Access restricts access to approved users/groups. Configure it for the Mai site.

1. Navigate to **Access** > **Applications** in your Cloudflare dashboard.
2. Click **Add an application**.
3. Select **SaaS** or **Self-hosted** (depending on your setup).
4. **Application domain:** `https://mai.<your-domain>` or Pages URL.
5. Create an access policy:
   - **Policy name:** `Dev team only`
   - **Rules:**
     - Include: `Emails` / `r-log`, `antz`, `madmax` (add allowlisted developers)
     - OR: `Emails ending in` `@yourdomain.com` (if using organization emails)
6. Click **Save** and **Finish**.

Cloudflare will now intercept requests, prompt for authentication (Google, GitHub, or email), and check against your allowlist.

---

## 7. Run as a service

The `mai.cli serve` command runs the refresh cron loop indefinitely, invoking the refresh cycle every `REFRESH_INTERVAL_SECONDS`.

### 7.1 Create a systemd unit file

Create `/etc/systemd/system/mai.service`:

```ini
[Unit]
Description=Mai port-debt tracker (refresh cron service)
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=3

[Service]
Type=simple
User=mai
WorkingDirectory=/srv/mai/app
Environment="PATH=/srv/mai/app/venv/bin"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/srv/mai/app/.env
ExecStart=/srv/mai/app/venv/bin/python -m mai.cli serve
Restart=on-failure
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mai

[Install]
WantedBy=multi-user.target
```

### 7.2 Create the service user

```bash
sudo useradd --system --home-dir /srv/mai/app --shell /usr/sbin/nologin mai
sudo chown -R mai:mai /srv/mai/app
sudo chown -R mai:mai /srv/mai/mirrors
```

### 7.3 Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable mai.service
sudo systemctl start mai.service

# Verify it's running
sudo systemctl status mai.service

# Tail logs
sudo journalctl -u mai.service -f
```

Expected log output:
```
serving: refresh every 3600s (Ctrl-C to stop)
refresh: +<N> commits, <M> repos harvested, <K> port candidates, <P> pages
refresh: +<N> commits, <M> repos harvested, <K> port candidates, <P> pages
...
```

### 7.4 Monitoring and restarts

The systemd unit is configured with automatic restart on failure:
- `Restart=on-failure`: restarts if the process exits with a non-zero code.
- `RestartSec=10`: waits 10 seconds between restarts.
- `StartLimitBurst=3`: allows up to 3 restart attempts within `StartLimitIntervalSec=300` (5 minutes).

To manually restart:
```bash
sudo systemctl restart mai.service
```

To view the last N lines of logs:
```bash
sudo journalctl -u mai.service -n 50
```

---

## Verification gates

These checks confirm the deployment is operational. Execute them after the runbook steps are complete:

### Gate 1: Site accessibility behind Cloudflare Access

- [ ] Navigate to `https://mai.<your-domain>` in a browser.
- [ ] Confirm you are prompted to authenticate (Google, GitHub, or email).
- [ ] After authentication, confirm the Mai site loads and displays content.
- [ ] Try accessing from an incognito/private window or different account — access should be denied.

### Gate 2: Port-debt data is populated

- [ ] Visit the site and navigate to `/port/` (or the equivalent port-debt board page).
- [ ] Confirm the page displays four target-fork columns (e.g., MaNGOS Three, MaNGOS Two, Trinity Core, CMaNGOS).
- [ ] Confirm at least one port candidate appears in at least one column.
- [ ] Verify commit counts and metadata are visible.

### Gate 3: Service is running

- [ ] On the deployment box, run:
  ```bash
  sudo systemctl status mai.service
  ```
- [ ] Confirm the status shows `active (running)` in green.
- [ ] Tail the logs to see refresh cycles:
  ```bash
  sudo journalctl -u mai.service -f
  ```

### Gate 4: Freshness proof — automatic refresh on commit

- [ ] In one of the watched fork repositories (e.g., mangosthree/server), push a trivial commit (e.g., update a comment or whitespace).
- [ ] Note the push timestamp.
- [ ] Wait for one `REFRESH_INTERVAL_SECONDS` (e.g., if set to 3600, wait up to 1 hour; reduce for testing).
- [ ] Check the Mai site for updated "last refresh" timestamp or "updated X minutes ago" text.
- [ ] Verify the new commit appears in the port-debt data.
- [ ] **Confirm this happened WITHOUT manually running `python -m mai.cli refresh`** (the cron service did it).

### Gate 5: Service resilience (auto-restart)

- [ ] Kill the Mai service process:
  ```bash
  sudo systemctl kill mai.service
  ```
- [ ] Wait 15 seconds.
- [ ] Verify the service has restarted:
  ```bash
  sudo systemctl status mai.service
  ```
- [ ] Confirm status is again `active (running)`.
- [ ] Confirm logs show the service restarted and resumed the refresh cycle.

---

## Troubleshooting

### Service fails to start

Check logs:
```bash
sudo journalctl -u mai.service -n 100 --priority=err
```

Common issues:
- **Missing `.env` file:** ensure `/srv/mai/app/.env` exists and is readable by the `mai` user.
- **Database connection fails:** verify `DATABASE_URL` points to a reachable Neon instance and credentials are correct.
- **Missing secrets:** ensure `GITHUB_TOKEN`, `OPENROUTER_API_KEY`, and `FIRECRAWL_API_KEY` are set.

### Refresh cycle hangs or is slow

- Check network connectivity: `curl https://api.github.com` should succeed.
- Verify API rate limits: large refreshes may take 15–30 minutes on first run.
- Review logs: `sudo journalctl -u mai.service -f`.

### Deploy script fails

- Ensure Hugo and Wrangler are installed: `hugo version && wrangler --version`.
- Test the script manually: `bash /srv/mai/app/scripts/deploy_site.sh`.
- Check Wrangler authentication: `wrangler whoami`.

### Git mirrors grow too large

The `mirrors/` directory grows as repositories are cloned. Monitor:
```bash
du -sh /srv/mai/mirrors
```

To manage space, you can prune old reflog entries:
```bash
cd /srv/mai/mirrors
for dir in */; do
  cd "$dir"
  git gc --aggressive
  cd ..
done
```
