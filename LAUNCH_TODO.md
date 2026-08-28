# Launch TODO — rate-limiting & cost hardening

Operational tasks that must be done **outside the app code** for the new rate limiting to be
trustworthy and to guarantee a runaway AWS bill can't happen. The app-level limits (Flask-Limiter,
Redis-backed) are already implemented and verified; these items make them effective in production.

Context: prod topology is **client → Cloudflare → nginx → Flask**. The limiter keys on the real
client IP from Cloudflare's `CF-Connecting-IP` header (configurable via `RATELIMIT_CLIENT_IP_HEADER`).

---

## 0. Security-audit operational follow-ups — do these BEFORE launch

App-code hardening from the security audit is done (headers, cookie flags, session rotation, account
lockout, invite-token no longer leaked, gunicorn prod compose, non-root container, pinned deps, Redis
auth). These remaining items can only be done outside the code:

- [x] **Rotate the AWS SES access key and the reCAPTCHA secret NOW.** They have lived in a plaintext
      `app_config/.env.dev` on a dev disk (not committed to git, but treat as exposed). Issue a new SES
      key pair, delete the old one, and regenerate the reCAPTCHA secret. (See *Secrets storage hardening*
      below for how to store the prod values.)
- [x] **Create `app_config/.env.prod`** (see `app_config/.env.example`). It MUST set `DEBUG=false`, a
      distinct `INVITE_JWT_SECRET`, `PUBLIC_BASE_URL` (the real https origin, for invite-email links), a
      strong `REDIS_PASSWORD`, and Redis URLs that carry that password (`redis://:PASSWORD@redis:6379/0`
      for Celery, `/1` for the limiter). `make prod-up` runs the full stack with this file.
- [x] **Verify the prod container runs non-root and not the dev server:** `make prod-up` then
      `docker compose ... exec web whoami` → `appuser`, and the web process is `gunicorn`, not `flask run`.
      *(Verified 2026-07-09 via an isolated prod-compose smoke test: `whoami` → `appuser`, PID 1 is
      gunicorn ×3 workers, `/healthz` → 200. Re-run the exec one-liner on the box after first real `prod-up`.)*
- [x] Ensure seed/bootstrap accounts with default passwords from `*_seed.json` are never created in prod.
      *(Verified 2026-07-09: `seed-db` is a dev-only Makefile target; the prod init service runs only
      `db upgrade` + `init-db` + `define-visuals`, and a fresh prod boot creates exactly one user — the
      bootstrap admin. `account_seed.json` stays on the dev disk only.)*
- [ ] **Set the site-admin removal password after deploy**: run `make prod-rotate-removal-password`
      once the stack is up (after `db upgrade` creates the `removal_password` table). The initial set
      is CLI-only — until it runs, the "Remove Site Admin" / "Removal password" flows refuse with a
      message pointing at this command. It prints a strong generated secret **once**; distribute it
      out-of-band to the select group of admins who should hold it (never by email/chat that persists).
      Re-run the same command any time a holder of the secret leaves (break-glass rotation, no current
      password needed).

### Secrets storage hardening
A plaintext `app_config/.env.prod` on the server is an accepted baseline for an app this size **only if
the file and the box are locked down**. Note the compose `env_file:` injects these as container
environment variables, so anyone who can read the file, SSH in, or run `docker` (the `docker` group is
root-equivalent) can also read every secret via `docker inspect` / `/proc/<pid>/environ`. Shrink that
blast radius:

- [ ] **Lock down the env file**: `chmod 600 app_config/.env.prod` and own it as the deploy user.
      Confirm it is gitignored (it is) and never copied into a world-readable backup.
- [ ] **Restrict server access**: minimize who can SSH in and who is in the `docker` group; require MFA
      on the AWS account. Keep Lightsail snapshots private (they contain the whole disk, env file included).
- [ ] **Scope the AWS key to least privilege — highest-value item.** Create a dedicated IAM user whose
      policy allows **only** sending email (ideally constrained to the verified sender), so a leaked key
      can at worst send email as you, not touch the rest of the account:
      ```json
      {
        "Version": "2012-10-17",
        "Statement": [{
          "Effect": "Allow",
          "Action": ["ses:SendEmail", "ses:SendRawEmail"],
          "Resource": "*",
          "Condition": {"StringEquals": {"ses:FromAddress": "no-reply@your-domain.ca"}}
        }]
      }
      ```
- [ ] **Static keys are unavoidable on Lightsail** — Lightsail instances can't attach an IAM role, so
      the app must use an IAM user access key (the least-privilege one above). If you later move the app
      to **EC2, ECS, or Fargate**, attach an **IAM instance/task role** instead and delete the AWS
      `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` from `.env.prod` entirely — boto3 picks up the role's
      short-lived, auto-rotating credentials with no code change. That removes the highest-value secret
      from disk and is the main reason to consider migrating off Lightsail.
- [ ] **Blank `BOOTSTRAP_ADMIN_PASSWORD`** in `.env.prod` after the first `prod-up` creates the admin —
      `init-db` only reads it once, so it doesn't need to persist.
- [ ] *(Optional, if multiple people administer the box)* move secrets into a manager — AWS SSM Parameter
      Store (SecureString) or Secrets Manager fit since you're already on AWS; SOPS-encrypted files or
      Doppler are alternatives. Nice-to-have for a single maintainer, worth it for a team (audit + rotation).

### nginx TLS front (now runs inside compose)
- [ ] **Install the origin TLS material** in `deploy/nginx/tls/` (gitignored) — see that folder's
      `README.md`: Cloudflare Origin Certificate (`origin.pem` + `origin.key`) and the Authenticated
      Origin Pull CA (`cloudflare-origin-pull-ca.pem`).
- [ ] **Cloudflare dashboard**: set SSL/TLS mode to **Full (strict)** (currently *Flexible*) and enable
      **Authenticated Origin Pulls** (zone-level). Then a direct-to-origin request without Cloudflare's
      client cert is refused by nginx (`ssl_verify_client on`).
- [ ] **Retire the host nginx** — the containerized `nginx` service now binds host ports 80/443. Stop/
      disable any host nginx so they don't conflict.
- [ ] **Refresh the Cloudflare IP list** in `deploy/nginx/canask.conf` (`set_real_ip_from …`) if
      Cloudflare changes ranges (https://www.cloudflare.com/ips/). *(Diffed against the published
      v4+v6 lists 2026-07-09: identical. Ongoing item — recheck occasionally.)*

### Data & bootstrap
- [ ] **Load chart data via a Postgres dump** (chosen strategy — prod does not run scrapers). Data launch:
      `make prod-db-up` → `make prod-restore DUMP=<dump.sql>` → `make prod-up`. Fresh launch: `make prod-up`
      alone bootstraps schema + admin + visual definitions (charts stay blank until a restore). Capture a
      dump from the populated source DB with `make prod-backup > canask-YYYY-MM-DD.sql`.
      *(Dump captured 2026-07-09 from the dev DB → `canask-2026-07-09.sql` in the repo root (gitignored):
      119 visuals, 12,299 data points, 7 sources, users table empty. Dumped with `--no-owner
      --no-privileges` so it restores cleanly even if prod's `DB_USER` differs from dev's. Remaining:
      scp it to the box and run the restore sequence.)*
- [ ] **Schedule regular backups**: host cron running `deploy/backup.sh` (gzip + S3 upload + local
      pruning — see `DEPLOY_LIGHTSAIL.md §9` for the cron line).
- [ ] Note: after `flask drop-db` you must `flask db stamp base` before `db upgrade` (Alembic stays
      stamped at head otherwise and rebuilds nothing).

---

## 1. Restrict the origin to Cloudflare only  — CRITICAL

**Why:** `CF-Connecting-IP` / `X-Forwarded-For` are just request headers. If the nginx origin is
reachable directly (bypassing Cloudflare), an attacker can spoof them and defeat per-IP limits — or
rotate the header to get unlimited requests. Per-IP limiting is only sound if traffic *must* pass
through Cloudflare.

- [ ] Firewall the server so ports 80/443 accept connections **only from Cloudflare's published IP
      ranges** (https://www.cloudflare.com/ips/). Use security-group / ufw / iptables rules, or
      nginx `allow`/`deny`.
- [ ] (Recommended) Enable **Cloudflare Authenticated Origin Pulls** (mTLS) so nginx only accepts
      TLS connections presenting Cloudflare's client cert — defeats attackers who discover the
      origin IP even within CF ranges.
- [ ] Verify: `curl` the origin IP directly (not via the domain) → should be refused/timeout.

## 2. Fix real client IP through the proxy chain

**Why:** `request.remote_addr` (used for audit logging) and the ProxyFix hop count must reflect the
real client, not nginx/Cloudflare. The app already trusts `TRUSTED_PROXY_COUNT` hops (default **2**
= Cloudflare + nginx).

- [x] Configure nginx `ngx_http_realip_module`: `set_real_ip_from <cloudflare ranges>;` and
      `real_ip_header CF-Connecting-IP;` so nginx-level logs/vars use the true client IP.
      *(In `deploy/nginx/canask.conf` — CF ranges at lines 24–45, `real_ip_header` at line 46.)*
- [x] Confirm nginx forwards `CF-Connecting-IP` (and `X-Forwarded-For`) unaltered to Flask.
      *(Confirmed: `proxy_set_header CF-Connecting-IP $http_cf_connecting_ip` passes it through, and
      `$proxy_add_x_forwarded_for` appends rather than overwrites.)*
- [ ] Confirm `TRUSTED_PROXY_COUNT` matches the actual `X-Forwarded-For` chain nginx produces
      (2 for CF+nginx; adjust if nginx overwrites vs appends XFF). Override via env if needed.
      *(On paper 2 is right: CF appends the client IP, nginx appends its realip-rewritten `$remote_addr`.
      Keep unticked until the deploy-time UserActivity check below confirms it live.)*
- [ ] Verify after deploy: a `UserActivity` login row shows the real client IP, not a proxy IP.

## 3. AWS Budget alarm  — hard cost backstop

**Why:** The ultimate guarantee against a surprise bill, independent of the app.

- [ ] Create an **AWS Budgets** monthly cost budget with an alert threshold (e.g. **$20/mo**,
      alert at 80% and 100%) emailing you.
- [ ] (Optional) Add a CloudWatch alarm on SES `Send` count for an early spike signal.

## 4. Cap AWS SES sending  — limit the blast radius

**Why:** `/feedback` sends via SES. Even if every app-level control were bypassed, a low SES quota
caps the total damage. The app also enforces a global **100 emails/day** cap on feedback
(`RATELIMIT_FEEDBACK_GLOBAL`), but SES-side limits are the true ceiling.

- [ ] Set the SES account **max send rate** and **daily sending quota** to the lowest values that
      still cover real feedback volume.
- [ ] Confirm SES is out of the sandbox only if you actually need to email arbitrary recipients
      (feedback currently mails a single hardcoded address, so sandbox may be fine).
- [ ] (Optional, defense-in-depth) Add a **Cloudflare rate-limiting rule** on `/feedback` and
      `/api/*` to shed abuse at the edge before it reaches the origin.

---

## 5. Domain migration: internal-use.dev → canask.ca (when the time comes)

The nginx `server_name` in `deploy/nginx/canask.conf` already answers for both domains, and the TLS
file *paths* don't change — only their contents. Everything else is per-Cloudflare-zone and must be
redone in the canask.ca zone:

- [ ] **DNS**: A record for `canask.ca` (+ `www`) → the static IP, proxy ON (orange cloud).
- [ ] **Origin certificate**: Origin Certificates are issued per zone, so the current `origin.pem`
      is valid for internal-use.dev only. Generate a new one from the **canask.ca** zone
      (SSL/TLS → Origin Server → Create Certificate) and overwrite `deploy/nginx/tls/origin.pem` +
      `origin.key` on the server. Skipping this = Cloudflare 526 errors under Full (strict).
- [ ] **Zone settings**: set SSL/TLS mode to **Full (strict)** and enable **Authenticated Origin
      Pulls** on the canask.ca zone (both are per-zone toggles).
- [ ] **App env**: update `PUBLIC_BASE_URL` in `app_config/.env.prod` (invite-email links) and any
      other domain-bearing values, then `make prod-up`.
- [ ] **Cleanup**: once canask.ca is confirmed serving, remove the internal-use.dev names from
      `server_name`, and retire the old zone's DNS record so the origin IP isn't advertised twice.

---

## Reference — app-side settings already in place
Tunable via env (defaults in `data_viz/config.py`; documented in `app_config/.env.example`):

| Setting | Default | What it limits |
|---|---|---|
| `RATELIMIT_FEEDBACK` | `5 per hour` | `/feedback` per IP |
| `RATELIMIT_FEEDBACK_GLOBAL` | `100 per day` | `/feedback` all IPs (deducts only on successful send) |
| `RATELIMIT_API` | `60 per minute` | `/api/v1/province/<p>/data` per IP |
| `RATELIMIT_LOGIN` | `10 per minute` | `/v1/login` POST per IP |
| `RATELIMIT_DEFAULT` | `200 per minute` | global default (all other routes) |
| `RATELIMIT_STORAGE_URI` | `redis://redis:6379/1` | limiter store (isolated from Celery on `/0`) |
| `RATELIMIT_CLIENT_IP_HEADER` | `CF-Connecting-IP` | which header holds the true client IP |
| `TRUSTED_PROXY_COUNT` | `2` | ProxyFix trusted hops (Cloudflare + nginx) |
| `RATELIMIT_ENABLED` | `true` | kill switch (set `false` to disable, e.g. in tests) |
