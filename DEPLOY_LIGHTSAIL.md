# CANASK deployment runbook (AWS Lightsail)

Deploy the full stack (nginx + web + worker + beat + Postgres + Redis) to a single Lightsail instance
running Docker Compose, behind Cloudflare. Everything here uses the compose files and `make` targets
already in the repo. For the security/pre-launch checklist see `LAUNCH_TODO.md`; for dev see `readme.md`.

> **Lightsail vs EC2**: this is the current, simplest path. Lightsail instances can't attach an IAM role,
> so AWS access uses a static least-privilege key and secrets live in a locked-down `app_config/.env.prod`
> on the box. If you'd rather have **no static AWS keys and a real secrets manager**, see
> **[DEPLOY_EC2.md](DEPLOY_EC2.md)** — same stack, EC2 + IAM instance role + Secrets Manager/SSM.

Topology: **client → Cloudflare (HTTPS) → nginx container (TLS, mTLS) → gunicorn**.

---

## Before you start

- A Cloudflare zone for your domain.
- The current populated database as a dump from wherever the data lives (prod does not run scrapers).
  `make prod-backup > canask.sql` works on a machine that has `app_config/.env.prod`; on the dev box,
  dump directly instead:
  `docker compose --env-file app_config/.env.dev exec -T db sh -c 'pg_dump -U $POSTGRES_USER $POSTGRES_DB' > canask.sql`
- Values for every key in `app_config/.env.example` (rotated secrets — see `LAUNCH_TODO.md §0`).

---

## 1. Provision the instance

- Lightsail → Create instance → **Linux/Unix → OS Only → Ubuntu 24.04 LTS**.
- Plan: **2 GB RAM minimum** (4 GB if Postgres + the worker feel tight).
- Create and **attach a static IP** (Networking tab) so DNS survives reboots.
- Enable **automatic snapshots** (Snapshots tab) — whole-disk disaster recovery, including the DB volume.

## 2. DNS + firewall

- **Cloudflare DNS**: add an `A` record for your domain → the static IP, **proxy ON** (orange cloud).
- **SSH lockdown** (Lightsail console → instance → Networking → IPv4 Firewall): restrict port **22** to
  your own IP.
- **80 / 443**: leave open in the Lightsail firewall (or restrict their *source* to Cloudflare's ranges
  — https://www.cloudflare.com/ips/ — which the Lightsail firewall enforces at the network edge).

> **Why not ufw for the 80/443 restriction?** Docker publishes container ports by writing iptables rules
> that **bypass the ufw INPUT chain**, so a `ufw deny` does *not* actually block traffic to nginx's
> published ports. Restrict source IPs at the **Lightsail console firewall** (edge-enforced) instead. The
> real gate is **Authenticated Origin Pulls (mTLS)** configured in step 6 — nginx refuses any TLS
> connection that doesn't present Cloudflare's client cert, so even a direct hit on the IP is rejected.

## 3. Install Docker

```
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER && newgrp docker   # log out/in afterwards so the group applies
sudo systemctl enable --now docker               # start on boot (pairs with the compose restart policies)
```

Optional but recommended on a 2 GB box — a swap file for build/scrape spikes:
```
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 4. Get the code

Use a read-only **deploy key** for the private repo (GitHub → repo → Settings → Deploy keys):
```
ssh-keygen -t ed25519 -C canask-deploy -f ~/.ssh/canask_deploy   # add the .pub to GitHub deploy keys
git -c core.sshCommand="ssh -i ~/.ssh/canask_deploy" clone git@github.com:<you>/CANASK.git
cd CANASK
```

## 5. Secrets + TLS certificates

1. Create `app_config/.env.prod` from `app_config/.env.example` and lock it down:
   ```
   cp app_config/.env.example app_config/.env.prod
   nano app_config/.env.prod            # fill in real values (see below)
   chmod 600 app_config/.env.prod
   ```
   Must set: `DEBUG=false`, `SECRET_KEY`, a distinct `INVITE_JWT_SECRET`, `PUBLIC_BASE_URL`
   (`https://your-domain`), `DB_*` + `DATABASE_URL`, a strong `REDIS_PASSWORD` **and** Redis URLs that
   carry it (`CELERY_BROKER_URL=redis://:PASSWORD@redis:6379/0`, result backend `/0`,
   `RATELIMIT_STORAGE_URI=redis://:PASSWORD@redis:6379/1`), `BOOTSTRAP_ADMIN_*`, AWS SES (`AWS_*`,
   `SES_SENDER_EMAIL`), and `RECAPTCHA_SECRET`.
2. Install the origin TLS material into `deploy/nginx/tls/` (see `deploy/nginx/tls/README.md`):
   `origin.pem`, `origin.key`, `cloudflare-origin-pull-ca.pem`.

## 6. Cloudflare settings

- **SSL/TLS → Overview**: set mode to **Full (strict)** (from *Flexible*).
- **SSL/TLS → Origin Server**: **Create Certificate** (this is your `origin.pem`/`origin.key`), and
  enable **Authenticated Origin Pulls** (zone-level).

## 7. First launch

**With existing data (recommended):**
```
scp -i ~/.ssh/... canask.sql ubuntu@<static-ip>:~/CANASK/       # copy your dump up (from your machine)
make prod-db-up                          # start only Postgres
make prod-restore DUMP=canask.sql        # load the dump into the fresh volume
make prod-up                             # bring up the rest (init's migrations no-op at head)
```

**Fresh / empty:**
```
make prod-up          # init bootstraps schema + admin + visual definitions; charts blank until a restore
```

## 8. Verify

```
curl -I https://your-domain/                       # 200 via Cloudflare
curl -k --resolve your-domain:443:<static-ip> https://your-domain/   # should be REFUSED (mTLS: no CF cert)
docker compose ... exec web whoami                 # -> appuser (non-root)
```
Then log in as the bootstrap admin and confirm a `UserActivity` login row shows the **real client IP**
(not an internal proxy IP). Send yourself a test invite and confirm the email link works.

## 9. Backups (automated)

`deploy/backup.sh` dumps the DB, gzips it, uploads off-box to S3, and prunes old local copies. It runs
independently of the app containers (DR-safe) — see the script header for config.

1. **IAM for backups** — a dedicated user (separate from the SES sender key) scoped to writing the
   backup bucket only:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["s3:PutObject"],
       "Resource": "arn:aws:s3:::your-backup-bucket/canask/*"
     }]
   }
   ```
   Configure it for the cron user: `aws configure` (or an env file). Add an S3 **lifecycle rule** on the
   bucket to expire old backups (e.g. 90 days).
2. **Schedule it** with cron (`crontab -e`):
   ```
   BACKUP_S3_URI=s3://your-backup-bucket/canask
   0 3 * * * cd /home/ubuntu/CANASK && ./deploy/backup.sh >> /home/ubuntu/canask-backup.log 2>&1
   ```
   Test it once by hand first: `BACKUP_S3_URI=s3://your-backup-bucket/canask ./deploy/backup.sh`.

## 10. Update / redeploy

```
cd ~/CANASK
git -c core.sshCommand="ssh -i ~/.ssh/canask_deploy" pull
make prod-up            # rebuilds images and recreates only changed containers
```

## 11. Rollback / recovery

- **App regression**: `git checkout <previous-tag> && make prod-up`.
- **Bad data / lost DB**: restore the newest dump —
  `make prod-down` → `make prod-db-up` → `make prod-restore DUMP=<dump>` → `make prod-up`.
  (Restore into a fresh volume; if the DB already has tables, `docker compose ... down -v` the db volume
  first, or restore a data-only dump.)
- **Instance lost**: create a new instance from the latest Lightsail snapshot, or re-run this runbook and
  restore the newest S3 dump.

> After `make drop-db` you must `flask db stamp base` before `db upgrade`, or Alembic stays stamped at
> head and rebuilds nothing.
