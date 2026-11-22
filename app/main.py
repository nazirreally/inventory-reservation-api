"""Transactional inventory reservation API, using SQLite for local reproducibility."""
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager, asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from .audit import reconcile

DB_PATH = os.environ.get('INVENTORY_DB', 'inventory.sqlite3')
@asynccontextmanager
async def lifespan(app):
    initialize()
    yield

app = FastAPI(title='Inventory Reservation API', version='1.0.0', lifespan=lifespan)

@contextmanager
def connection():
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA journal_mode=WAL')
    try:
        yield db
    finally:
        db.close()

def initialize():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (sku TEXT PRIMARY KEY, stock INTEGER NOT NULL CHECK(stock >= 0));
        CREATE TABLE IF NOT EXISTS reservations (
            id TEXT PRIMARY KEY, sku TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
            status TEXT NOT NULL CHECK(status IN ('active','confirmed','released','expired')),
            expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
            FOREIGN KEY(sku) REFERENCES inventory(sku));
        CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, reservation_id TEXT NOT NULL,
            FOREIGN KEY(reservation_id) REFERENCES reservations(id));
        CREATE INDEX IF NOT EXISTS idx_reservation_expiry ON reservations(status, expires_at);
        """)

class StockInput(BaseModel):
    sku: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    stock: int = Field(ge=0)

class ReserveInput(BaseModel):
    sku: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+    ttl_seconds: int = Field(default=900, ge=1, le=86400)

def expire(db, now):
    db.execute("UPDATE reservations SET status='expired' WHERE status='active' AND expires_at <= ?", (now,))

def available(db, sku, now):
    row = db.execute('SELECT stock FROM inventory WHERE sku=?', (sku,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Unknown SKU')
    held = db.execute("SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE sku=? AND status='active' AND expires_at > ?", (sku, now)).fetchone()[0]
    return row['stock'] - held

@app.put('/inventory')
def put_inventory(item: StockInput):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        now = int(time.time())
        expire(db, now)
        current = db.execute('SELECT stock FROM inventory WHERE sku=?', (item.sku,)).fetchone()
        if current:
            held = db.execute("SELECT COALESCE(SUM(quantity),0) FROM reservations WHERE sku=? AND status='active'", (item.sku,)).fetchone()[0]
            if item.stock < held:
                db.rollback()
                raise HTTPException(409, 'Stock cannot be lower than active reservations')
        db.execute('INSERT INTO inventory(sku,stock) VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET stock=excluded.stock', (item.sku,item.stock))
        db.commit()
        return {'sku': item.sku, 'stock': item.stock}

@app.get('/inventory/{sku}')
def get_inventory(sku: str):
    with connection() as db:
        now = int(time.time())
        return {'sku':sku, 'available':available(db,sku,now)}

@app.post('/reservations', status_code=201)
def reserve(item: ReserveInput, idempotency_key: str = Header(..., min_length=1, max_length=120)):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT reservation_id FROM idempotency WHERE key=?', (idempotency_key,)).fetchone()
        if existing:
            row = db.execute('SELECT * FROM reservations WHERE id=?', (existing[0],)).fetchone()
            if (row['sku'] != item.sku or row['quantity'] != item.quantity
                    or row['expires_at'] - row['created_at'] != item.ttl_seconds):
                db.rollback()
                raise HTTPException(409, 'Idempotency key reused with different reservation')
            db.commit()
            return dict(row)
        now = int(time.time())
        expire(db, now)
        if available(db,item.sku,now) < item.quantity:
            db.rollback()
            raise HTTPException(409, 'Insufficient available stock')
        rid = str(uuid.uuid4())
        db.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?)', (rid,item.sku,item.quantity,'active',now+item.ttl_seconds,now))
        db.execute('INSERT INTO idempotency VALUES(?,?)', (idempotency_key,rid))
        db.commit()
        return {'id':rid,'sku':item.sku,'quantity':item.quantity,'status':'active','expires_at':now+item.ttl_seconds,'created_at':now}

@app.post('/reservations/{rid}/confirm')
def confirm(rid: str):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        now = int(time.time())
        expire(db, now)
        row = db.execute('SELECT * FROM reservations WHERE id=?', (rid,)).fetchone()
        if not row:
            db.rollback(); raise HTTPException(404, 'Unknown reservation')
        if row['status'] == 'confirmed':
            db.commit(); return {'id':rid,'status':'confirmed'}
        if row['status'] != 'active':
            db.rollback(); raise HTTPException(409, f"Cannot confirm {row['status']} reservation")
        db.execute('UPDATE inventory SET stock=stock-? WHERE sku=?', (row['quantity'],row['sku']))
        db.execute("UPDATE reservations SET status='confirmed' WHERE id=?", (rid,))
        db.commit()
        return {'id':rid,'status':'confirmed'}

@app.post('/reservations/{rid}/release')
def release(rid: str):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM reservations WHERE id=?', (rid,)).fetchone()
        if not row:
            db.rollback(); raise HTTPException(404, 'Unknown reservation')
        expire(db, int(time.time()))
        row = db.execute('SELECT status FROM reservations WHERE id=?', (rid,)).fetchone()
        if row['status'] == 'active':
            db.execute("UPDATE reservations SET status='released' WHERE id=?", (rid,))
            db.commit(); return {'id':rid,'status':'released'}
        if row['status'] == 'released':
            db.commit(); return {'id':rid,'status':'released'}
        db.rollback(); raise HTTPException(409, f"Cannot release {row['status']} reservation")

@app.get('/health/inventory')
def inventory_health():
    """Read-only inventory reconciliation; suitable for internal operations."""
    with connection() as db:
        db.execute('BEGIN')
        report = reconcile(db, int(time.time()))
        db.commit()
        return report
