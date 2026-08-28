# Development
dev-up:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-down:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml down

web-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f web

worker-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f worker

# Tests. The `test` service image layers pytest onto the webapp requirements
# (docker-compose.test.yml); tests/conftest.py rewrites the database name in
# DATABASE_URL to <name>_test before the app imports, so the dev DB is never touched.
# `run --rm` works without a running dev stack (compose auto-starts db) and shares the
# dev compose project, so an already-running db container is reused.
TESTC = docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.test.yml

# Full suite. Usage: make test          -> everything
#                    make test k=das    -> pytest -k "das"
test:
	$(TESTC) run --rm test pytest -v $(if $(k),-k "$(k)")

# Pure unit tests only: no DB started, fast inner loop.
test-fast:
	$(TESTC) run --rm --no-deps test pytest tests/unit -v

# Full suite with a line-by-line coverage report.
test-cov:
	$(TESTC) run --rm test pytest -v --cov=data_viz --cov-report=term-missing

# Full suite + report files: htmlcov/index.html (browsable per-line coverage) and
# pytest-results.xml (junit, same format CI uploads). Both are gitignored.
test-report:
	$(TESTC) run --rm test pytest -v --cov=data_viz --cov-report=html --junitxml=pytest-results.xml
	@echo ""
	@echo "Coverage report: htmlcov/index.html"
	@echo "JUnit results:   pytest-results.xml"

# Production. APP_ENV_FILE makes the base compose load .env.prod (not .env.dev) into the app services;
# the prod override adds the nginx TLS front, the one-shot init bootstrap, Redis auth, and restart
# policies. The init service auto-runs `db upgrade` + `init-db` + `define-visuals` on `prod-up`.
PROD = APP_ENV_FILE=app_config/.env.prod docker compose --env-file app_config/.env.prod -f docker-compose.yml -f docker-compose.prod.yml

prod-up:
	$(PROD) up --build -d

prod-down:
	$(PROD) down

prod-logs:
	$(PROD) logs -f

# Bring up ONLY the database first (for restoring a dump into a fresh volume before the app starts).
prod-db-up:
	$(PROD) up -d db

# Load a Postgres dump captured with `make prod-backup` (or pg_dump elsewhere). Usage: make prod-restore DUMP=backup.sql
# ON_ERROR_STOP + --single-transaction: psql's default is to keep going past failed statements and
# exit 0, which would report a silently partial restore as success.
prod-restore:
	$(PROD) exec -T db sh -c 'psql -v ON_ERROR_STOP=1 --single-transaction -U $$POSTGRES_USER $$POSTGRES_DB' < "$(DUMP)"

# Dump the running database to stdout -> a file. Usage: make prod-backup > canask-$(date +%F).sql
# The @ is load-bearing: without it make echoes the recipe to stdout, which lands as the first line
# of the redirected dump and corrupts every backup (deploy/backup.sh pipes this into gzip).
prod-backup:
	@$(PROD) exec -T db sh -c 'pg_dump -U $$POSTGRES_USER $$POSTGRES_DB'

# Manual maintenance against prod (the init service already runs these on prod-up).
prod-migrate:
	$(PROD) exec web flask db upgrade

prod-build-visuals:
	$(PROD) exec web flask define-visuals
	$(PROD) exec web flask gen-visuals

prod-clear-invites:
	$(PROD) exec web flask clear-invites $(if $(email),--email "$(email)")

# Break-glass set/rotate of the site-admin removal password. Default: generates and prints a strong
# secret once. Usage: make prod-rotate-removal-password [password=...]
prod-rotate-removal-password:
	$(PROD) exec web flask rotate-removal-password $(if $(password),--password "$(password)")

clear-cache:
	docker compose --env-file app_config/.env.dev exec web rm -rf data_viz/static/.webassets-cache
	docker compose --env-file app_config/.env.dev exec web rm -f data_viz/static/assets/main.css
	docker compose --env-file app_config/.env.dev exec web rm -f data_viz/static/assets/main.js

# Database
new-migration:
	docker compose --env-file app_config/.env.dev exec web flask db migrate -m "$(msg)"
	docker compose --env-file app_config/.env.dev exec web flask db upgrade

migrate:
	docker compose --env-file app_config/.env.dev exec web flask db upgrade

seed:
	docker compose --env-file app_config/.env.dev exec web flask seed-db

define-visuals:
	docker compose --env-file app_config/.env.dev exec web flask define-visuals

gen-visuals:
	docker compose --env-file app_config/.env.dev exec web flask gen-visuals

# Sync visual definitions from the manifests, then (re)generate their data points.
build-visuals: define-visuals gen-visuals

# Ingest the monthly Drug Analysis Service workbook into the das_* row-level tables.
ingest-das:
	docker compose --env-file app_config/.env.dev exec web flask ingest-das

# Rebuild the DAS city gazetteer (static/assets/das_city_coords.json) after an ingest
# introduces new cities. Needs output/geonames_CA.zip (GeoNames CA dump).
build-das-gazetteer:
	docker compose --env-file app_config/.env.dev exec web flask build-das-gazetteer

init-db:
	docker compose --env-file app_config/.env.dev exec web flask init-db

drop-db:
	docker compose --env-file app_config/.env.dev exec web flask drop-db

# Purge revoked invites (they're hidden from the UI but linger in the DB).
# Usage: make clear-invites            -> delete all revoked invites
#        make clear-invites email=x@y  -> delete ALL invites for that email, any status
clear-invites:
	docker compose --env-file app_config/.env.dev exec web flask clear-invites $(if $(email),--email "$(email)")

# Break-glass set/rotate of the site-admin removal password (dev).
# Usage: make rotate-removal-password            -> generate + print a strong secret once
#        make rotate-removal-password password=X -> use an explicit value (lands in shell history)
rotate-removal-password:
	docker compose --env-file app_config/.env.dev exec web flask rotate-removal-password $(if $(password),--password "$(password)")
