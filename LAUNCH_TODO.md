# Launch TODO — rate-limiting & cost hardening

Operational tasks that must be done **outside the app code** for the new rate limiting to be
trustworthy and to guarantee a runaway AWS bill can't happen. The app-level limits (Flask-Limiter,
Redis-backed) are already implemented and verified; these items make them effective in production.

Context: prod topology is **client → Cloudflare → nginx → Flask**. The limiter keys on the real
client IP from Cloudflare's `CF-Connecting-IP` header (configurable via `RATELIMIT_CLIENT_IP_HEADER`).

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

- [ ] Configure nginx `ngx_http_realip_module`: `set_real_ip_from <cloudflare ranges>;` and
      `real_ip_header CF-Connecting-IP;` so nginx-level logs/vars use the true client IP.
- [ ] Confirm nginx forwards `CF-Connecting-IP` (and `X-Forwarded-For`) unaltered to Flask.
- [ ] Confirm `TRUSTED_PROXY_COUNT` matches the actual `X-Forwarded-For` chain nginx produces
      (2 for CF+nginx; adjust if nginx overwrites vs appends XFF). Override via env if needed.
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
