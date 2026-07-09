# Development
dev-up:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-down:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml down

web-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f web

worker-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f worker

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

init-db:
	docker compose --env-file app_config/.env.dev exec web flask init-db

drop-db:
	docker compose --env-file app_config/.env.dev exec web flask drop-db

# Purge revoked invites (they're hidden from the UI but linger in the DB).
# Usage: make clear-invites            -> delete all revoked invites
#        make clear-invites email=x@y  -> delete ALL invites for that email, any status
clear-invites:
	docker compose --env-file app_config/.env.dev exec web flask clear-invites $(if $(email),--email "$(email)")