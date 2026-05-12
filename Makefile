# Development
dev-up:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml up --build

dev-down:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml down

web-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f web

worker-logs:
	docker compose --env-file app_config/.env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f worker

# Production-like
prod-up:
	docker compose --env-file app_config/.env.prod up --build

prod-down:
	docker compose --env-file app_config/.env.prod down

prod-logs:
	docker compose --env-file app_config/.env.prod logs -f

# Database
migrate:
	docker compose --env-file app_config/.env.dev exec web flask db upgrade

seed:
	docker compose --env-file app_config/.env.dev exec web flask seed-db

init-db:
	docker compose --env-file app_config/.env.dev exec web flask init-db