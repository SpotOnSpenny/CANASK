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

Automated sources (see the registry in `data_scraping/registry.py`) refresh themselves every night —
`beat` enqueues a scrape per enabled source at 01:00 America/Edmonton, validation runs in the
`scrape-worker` container, and a clean result publishes itself (if the source has auto-publish on) or
waits as a **held** run for a site admin's decision. The nightly report (06:00) summarizes what ran, what
updated, and anything that needs attention — that's usually all "Data-only update" requires: **do
nothing and read tomorrow's email**, or open `/v1/admin/data-updates` and click **Publish** on a held run.

For the two sources with no scraper (`drugChecking`, `nationalDAS`) — or to push a source's data sooner
than its nightly slot, or to get a file in front of tier-1 validation before committing to a schedule —
**upload it on the Data Updates page** instead:

1. **Ingest and spot-check locally first**, in dev, before touching prod (upload it there too, or use
   `make ingest-das` / `make build-visuals` as before — dev is unaffected by any of this).

2. **DAS-specific — refresh the city gazetteer if new cities appeared:**
   ```
   make build-das-gazetteer
   ```
   Hand-add any stragglers it prints to `MANUAL_COORDS` in `data_viz/das_gazetteer.py` and re-run.
   Commit and push the refreshed `data_viz/static/assets/das_city_coords.json` (and any
   `MANUAL_COORDS` edit) — it is a **git-tracked asset**, not something rebuilt against prod. It
   reaches prod via Procedure A on the next code update.

3. **Back up prod** (see [Always: back up first](#always-back-up-first)) — belt-and-suspenders; a bad
   upload only ever produces a `held`/`rejected` run, never touches the live data, but the habit is cheap.

4. **Upload on the Data Updates page**: sign in as a site admin, open `/v1/admin/data-updates`, find the
   source's row, click **Upload**, pick the file and its `data_until` date. The request itself only does
   quick checks (extension, size) and stores the file to the S3 archive's `incoming/` prefix; the new run
   appears in the log as **Validating…** and polls every 5s until it resolves:
   - **Rejected** — tier-1 structure check failed (missing/renamed column, bad sheet); the run shows the
     problem and, for a renamed column, a same-string-similarity hint. Fix the source file or the
     contract/cleaner (see `CLAUDE.md`'s Scrape pipeline section) and re-upload.
   - **Held** — passed validation but needs a decision (first run for a source, a tier-2 warning, or
     auto-publish off for that source). Review the check results on the row, then **Publish** or
     **Discard**.
   - **Published** — auto-publish was on and every check passed; the affected dashboards are already
     rebuilt.

5. **Verify on the live site** — load the relevant dashboard or DAS Explorer page and confirm the new
   data renders: date range, row counts, and a couple of spot-checked values. **Roll back** (DAS:
   **Replay to here**) from the run log if something's wrong — no restore needed, the previous archived
   version is still there.

### Fallback: manual file transfer (no scraper/upload path available)

If you need to bypass the web upload entirely — e.g. debugging the pipeline itself, or a file too large
for a comfortable browser upload — the old scp path still works for getting a file onto the box, but it
no longer ingests itself:

```
scp -i <your-login-key> <file> ubuntu@<static-ip>:~/CANASK/output/    # your own login key, not canask_deploy
make prod-copy-output file=<filename>                                 # host output/ -> the web container
```

From there, run the pre-pipeline CLI commands directly against the container's `output/` copy — this
bypasses the pipeline's validation and S3 archiving entirely, so reserve it for troubleshooting the
pipeline itself, not routine updates:
```
make prod-ingest-das file=<filename>          # DAS workbook, cumulative
make prod-build-visuals                       # any other V1 source: define-visuals then gen-visuals
```
These CLI commands don't create a `ScrapeRun` or archive the file to S3 — they're a direct line to
`gen-visuals`/`ingest-das`, same as before this pipeline existed. Prefer the Upload modal (step 4 above,
uploading straight from your own machine) so the run is recorded, checked, and shows up in the nightly
report.

## C — Combined update

Run **Procedure A first** (so any new cleaner code, manifest changes, or migrations are live), then
**Procedure B** (so the new data is cleaned/ingested with the new code already in place).

## If something goes wrong

- `ingest-das`, `define-visuals`, and `gen-visuals` are all idempotent — a bad data-only update can
  usually be fixed by re-running the same command with corrected input, rather than a full restore.
- For anything more serious (bad app release, corrupted data you can't re-derive, lost instance), see
  `DEPLOY_LIGHTSAIL.md` §11 (Rollback / recovery).
