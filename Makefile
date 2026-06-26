.PHONY: dev docker logs

## Local dev (no Docker — inline tasks, SQLite/local storage)
dev:
	./scripts/dev-local.sh

## Full prod stack (Celery + Postgres + Redis + MinIO)
docker:
	docker-compose up --build

## Tail logs from all docker services
logs:
	docker-compose logs -f
