# CANASK

A Flask web app that visualizes Canadian drug-toxicity / substance-harm data by province. It runs as
a small set of Docker services — `web` (the Flask app under gunicorn), `worker` + `beat` (Celery
background jobs), `db` (Postgres), and `redis` — with an `nginx` TLS front added in production. All
common tasks are wrapped in the `Makefile`.

## Prerequisites

- Docker + Docker Compose (v2, the `docker compose` subcommand).
- `make`.
- Environment files under `app_config/` (gitignored). Copy `app_config/.env.example` and fill it in:
  - `app_config/.env.dev` for development.
  - `app_config/.env.prod` for production.

Required keys are documented in `app_config/.env.example`. At minimum you need `SECRET_KEY`,
`DB_NAME`/`DB_USER`/`DB_PASSWORD`, `DATABASE_URL`, `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, and the
`BOOTSTRAP_ADMIN_*` values used to create the first admin account.

---

## Development

Dev uses `app_config/.env.dev` and layers `docker-compose.dev.yml` on top of the base compose file:
it runs Flask's auto-reloading dev server, bind-mounts your source for live edits, and exposes ports
to the host (`web` on **5001**, Postgres on 5432, Redis on 6379).

### 1. Start the stack

```
make dev-up
```

This builds and starts `web`, `worker`, `beat`, `db`, and `redis`. The app is served at
**http://localhost:5001**.

### 2. Bootstrap the database (first run)

The dev stack does **not** auto-migrate. On a fresh database, run these once (in a second terminal,
while `dev-up` is running):

```
make migrate          # flask db upgrade — create the schema
make init-db          # create the bootstrap admin from BOOTSTRAP_ADMIN_* in .env.dev
make seed             # optional: load groups/users/sources from app_config/seed.json (if present)
make build-visuals    # define-visuals + gen-visuals — load chart definitions and data
```

`make build-visuals` needs scraped source files in the `output/` directory (gitignored). Any source
whose file is missing is skipped, so the app still runs — those charts are just empty.

### 3. Everyday commands

```
make web-logs         # follow the web service logs
make worker-logs      # follow the worker logs
make clear-cache      # wipe the built CSS/JS bundles + webassets cache
make dev-down         # stop and remove the dev stack
```

Run any Flask CLI command directly in the container:

```
docker compose --env-file app_config/.env.dev exec web flask <command>
```

---

## Production

For a full step-by-step server deploy (provisioning, DNS/firewall, certificates, first launch,
backups, updates, rollback) see **[DEPLOY_LIGHTSAIL.md](DEPLOY_LIGHTSAIL.md)** (current path) or
**[DEPLOY_EC2.md](DEPLOY_EC2.md)** (EC2 + IAM instance role + a secrets manager, no static AWS keys).
The summary below is the compose/`make` reference.

Production uses `app_config/.env.prod` and layers `docker-compose.prod.yml`, which adds:

- **nginx** terminating TLS on ports 80/443 in front of gunicorn (topology: Cloudflare → nginx → web).
- a one-shot **`init`** service that auto-runs `db upgrade` + `init-db` + `define-visuals` before the
  app starts, so the stack comes up already bootstrapped.
- **Redis authentication**, container **restart policies**, and log rotation.

`web`, `db`, and `redis` are internal to the compose network — only nginx publishes host ports.

### 1. One-time prerequisites

1. **Create `app_config/.env.prod`** from `app_config/.env.example`. It **must** set `DEBUG=false`, a
   strong `REDIS_PASSWORD` (and Redis URLs that carry it, e.g. `redis://:PASSWORD@redis:6379/0`), a
   distinct `INVITE_JWT_SECRET`, and `PUBLIC_BASE_URL` (your https origin, used in invite emails).
2. **Install the TLS material** in `deploy/nginx/tls/` (gitignored) — see `deploy/nginx/tls/README.md`:
   the Cloudflare Origin Certificate (`origin.pem` + `origin.key`) and the Authenticated Origin Pull CA
   (`cloudflare-origin-pull-ca.pem`).
3. **Cloudflare dashboard**: set SSL/TLS to **Full (strict)** and enable **Authenticated Origin Pulls**.
   Firewall ports 80/443 to Cloudflare's IP ranges. See `LAUNCH_TODO.md` for the full checklist.

### 2. Launch

**Fresh site (no data yet):**

```
make prod-up
```

The `init` service migrates the schema, creates the admin, and syncs visual definitions; then the app
comes up. Charts stay empty until data is loaded.

**Launching with chart data (restore a Postgres dump):**

```
make prod-db-up                       # start only the database
make prod-restore DUMP=canask.sql     # load a dump captured with `make prod-backup`
make prod-up                          # bring up the rest (init's migrations no-op at head)
```

Capture a dump from a populated database with:

```
make prod-backup > canask-$(date +%F).sql
```

### 3. Everyday commands

```
make prod-logs        # follow all prod service logs
make prod-migrate     # run migrations manually (init already does this on prod-up)
make prod-down        # stop and remove the prod stack
```

After deploy, verify: hit your domain through Cloudflare (HTTPS 200), and confirm a direct-to-origin
request without Cloudflare's client cert is refused.

---

## Makefile reference

| Target | Environment | What it does |
|---|---|---|
| `dev-up` / `dev-down` | dev | Start / stop the dev stack (Flask dev server, port 5001) |
| `web-logs` / `worker-logs` | dev | Follow a service's logs |
| `migrate` | dev | `flask db upgrade` |
| `new-migration msg="..."` | dev | Create a migration, then upgrade |
| `init-db` | dev | Create the bootstrap admin |
| `seed` | dev | Seed users/groups/sources from `seed.json` |
| `define-visuals` / `gen-visuals` / `build-visuals` | dev | Load visual definitions and/or data |
| `clear-cache` | dev | Wipe built CSS/JS bundles + webassets cache |
| `drop-db` | dev | Drop all tables (then `flask db stamp base` before re-upgrading) |
| `prod-up` / `prod-down` | prod | Start (auto-bootstrap + nginx) / stop the prod stack |
| `prod-logs` | prod | Follow prod logs |
| `prod-db-up` | prod | Start only the database (for restoring before the app starts) |
| `prod-restore DUMP=<file>` | prod | Load a Postgres dump |
| `prod-backup` | prod | Dump the database to stdout |
| `prod-migrate` / `prod-build-visuals` | prod | Manual migrations / visual rebuild |
