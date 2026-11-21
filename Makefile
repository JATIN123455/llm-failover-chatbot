.PHONY: up down test logs

up:
	docker compose up --build -d

down:
	docker compose down

test:
	cd backend && python -m pytest tests

logs:
	docker compose logs -f