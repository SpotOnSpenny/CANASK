# CANASK — updating production

Day-to-day companion to `DEPLOY_LIGHTSAIL.md` (first-time provisioning, TLS, disaster recovery) and
`LAUNCH_TODO.md` (security checklist). This doc is the "how do I ship a change to prod" reference —
**keep it updated as the process changes** (new Makefile targets, new data sources, new conventions).

All `make prod-*` commands are run **on the prod host itself**, from `~/CANASK` (same as
`DEPLOY_LIGHTSAIL.md`), not remotely.

There are three kinds of update:

- **[A — Code-only](#a--code-only-update)**: app/template/JS changes, migrations, manifest edits, an
  already-committed gazetteer refresh. No new raw source data.
- **[B — Data-only](#b--data-only-update)**: a newly scraped/ingested source file (DAS workbook, drug
  checking export, a provincial scrape) needs to land in prod's database. No code changes.
- **[C — Combined](#c--combined-update)**: both at once (e.g. a new cleaner *and* the data it cleans).

## Always: back up first

Regardless of which procedure you're running:

```
make prod-backup > canask-$(date +%F).sql
```

## A — Code-only update

```
cd ~/CANASK
git -c core.sshCommand="ssh -i ~/.ssh/canask_deploy" pull
make prod-up            # rebuilds images and recreates only changed containers
```

The `init` service (part of `prod-up`) automatically runs `db upgrade`, `init-db`, and
`define-visuals`, so schema migrations and manifest changes in `app_config/visuals/*.json` are picked
up on every `prod-up` — no separate step needed.

If something regresses, see `DEPLOY_LIGHTSAIL.md` §11 (Rollback / recovery).

## B — Data-only update

New source data lands in prod's own database by running the same idempotent ingestion CLI commands
against prod's `web` container that you already ran locally — **not** by dumping/restoring the whole
database, which would overwrite prod's live Users/Groups/Invites/UserActivity/site-admin-key state
with dev's. `ingest-das`, `define-visuals`, and `gen-visuals` only ever touch the pipeline-owned tables
(`DataPoints`, `DataSources`, `VisualQuery`, `Visuals`, `das_*`), so running them against prod is safe.

1. **Ingest and spot-check locally first**, in dev, before touching prod.

2. **DAS-specific — refresh the city gazetteer if new cities appeared:**
   ```
   make build-das-gazetteer
   ```
   Hand-add any stragglers it prints to `MANUAL_COORDS` in `data_viz/das_gazetteer.py` and re-run.
   Commit and push the refreshed `data_viz/static/assets/das_city_coords.json` (and any
   `MANUAL_COORDS` edit) — it is a **git-tracked asset**, not something rebuilt against prod. It
   reaches prod via Procedure A on the next code update.

3. **Back up prod** (see [Always: back up first](#always-back-up-first)).

4. **Transfer the new source file(s)** from your machine to the prod host's `output/` directory:
   ```
   scp -i <your-login-key> <file> ubuntu@<static-ip>:~/CANASK/output/
   ```
   This is your own login key for the box, **not** the read-only `~/.ssh/canask_deploy` git deploy key.

5. **On the prod host**, copy the file from the host into the running `web` container (prod has no
   bind mount, and `.dockerignore` excludes `output/` from the image, so it won't appear there any
   other way):
   ```
   make prod-copy-output file=<filename>
   ```

6. **Run the matching ingestion command:**
   - DAS workbook:
     ```
     make prod-ingest-das                       # ingests every nationalDAS.xlsx in output/, oldest first
     make prod-ingest-das file=<filename>        # or just the one you just copied in
     ```
   - Any other V1 source (drug checking, provincial scrapes, etc.):
     ```
     make prod-build-visuals                    # define-visuals then gen-visuals, all targets
     ```
     To regenerate just one target/province instead of everything, there's no wrapped Makefile
     target — use the raw command:
     ```
     docker compose --env-file app_config/.env.prod -f docker-compose.yml -f docker-compose.prod.yml \
       exec web flask gen-visuals --only <target>
     ```

7. **Verify on the live site** — load the relevant dashboard or DAS Explorer page and confirm the new
   data renders: date range, row counts, and a couple of spot-checked values.

## C — Combined update

Run **Procedure A first** (so any new cleaner code, manifest changes, or migrations are live), then
**Procedure B** (so the new data is cleaned/ingested with the new code already in place).

## If something goes wrong

- `ingest-das`, `define-visuals`, and `gen-visuals` are all idempotent — a bad data-only update can
  usually be fixed by re-running the same command with corrected input, rather than a full restore.
- For anything more serious (bad app release, corrupted data you can't re-derive, lost instance), see
  `DEPLOY_LIGHTSAIL.md` §11 (Rollback / recovery).
