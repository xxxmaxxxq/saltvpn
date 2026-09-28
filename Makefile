# ═══════════════════════════════════════════════════════════════════════════
#  Makefile — короткие команды вместо длинных docker compose
#  Запуск:  make up   /   make logs   /   make migrate
# ═══════════════════════════════════════════════════════════════════════════
COMPOSE = docker compose -f docker/docker-compose.yml --env-file .env

.PHONY: help up down restart logs ps migrate makemigration shell-api shell-db test lint fmt backup

help:            ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up:              ## Поднять весь стек
	$(COMPOSE) up -d --build

down:            ## Остановить стек (данные сохраняются)
	$(COMPOSE) down

restart:         ## Перезапустить бота и API (после правки конфигов)
	$(COMPOSE) restart api bot worker

logs:            ## Смотреть логи (Ctrl+C чтобы выйти)
	$(COMPOSE) logs -f --tail=100 api bot worker

ps:              ## Статус контейнеров
	$(COMPOSE) ps

migrate:         ## Применить миграции БД
	$(COMPOSE) exec api alembic upgrade head

makemigration:   ## Создать миграцию: make makemigration m="описание"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

shell-api:       ## Зайти в контейнер API
	$(COMPOSE) exec api bash

shell-db:        ## Открыть psql
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

test:            ## Прогнать тесты
	$(COMPOSE) exec api pytest -q

lint:            ## Проверить код линтером
	$(COMPOSE) exec api ruff check app

fmt:             ## Отформатировать код
	$(COMPOSE) exec api ruff format app

backup:          ## Сделать дамп базы в ./backups
	bash scripts/backup-db.sh
