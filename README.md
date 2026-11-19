# Inventory Reservation API

A standalone FastAPI backend demonstrating **transactional inventory holds**, **idempotent reservation requests**, **expiry**, and **order confirmation** with a durable SQLite database.

## Business problem

An online store must temporarily reserve items while a shopper checks out. Concurrent requests must not oversell, retries must not duplicate holds, and abandoned holds must expire.

## Features
- Atomic reservations using SQLite `BEGIN IMMEDIATE` write transactions
- Unique idempotency keys for safe request retries
- Time-limited stock holds with lazy expiry
- Confirm once (decrement physical stock exactly once) or release
- Reject stock updates that would violate active holds
- Input validation and interactive OpenAPI UI
- Docker Compose, pytest, GitHub Actions

## Quick start

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for Swagger UI. Alternatively run `docker compose up --build`.

## Example workflow

```bash
curl -X PUT http://localhost:8000/inventory -H 'Content-Type: application/json' -d '{"sku":"coffee","stock":10}'
curl -X POST http://localhost:8000/reservations -H 'Content-Type: application/json' -H 'Idempotency-Key: order-1001' -d '{"sku":"coffee","quantity":2,"ttl_seconds":900}'
curl http://localhost:8000/inventory/coffee
```

Confirm with POST /reservations/{id}/confirm or release with POST /reservations/{id}/release.

## API contract

| Method | Endpoint | Purpose |
|---|---|---|
| PUT | /inventory | Set physical stock while preserving active holds |
| GET | /inventory/{sku} | Available stock |
| POST | /reservations | Create hold, requires Idempotency-Key header |
| POST | /reservations/{id}/confirm | Deduct stock once and finalize |
| POST | /reservations/{id}/release | Release active hold |

## Correctness

Physical stock is stored in inventory. Active reservations are counted as held until confirmation or expiry. SQLite serializes writers with BEGIN IMMEDIATE. Confirmation deducts stock and changes status atomically. Expired holds are ignored in availability calculations. Idempotency keys are globally unique and preserve the original reservation.

## Limitations

Single-instance reference implementation; no authentication, payments, rate limiting, distributed queue, or tenant-scoped keys. A production deployment would use PostgreSQL, authentication, observability, and load testing.

## Testing

Run `pytest -q`. CI tests Python 3.11 and 3.12.

## License

MIT (see LICENSE).
