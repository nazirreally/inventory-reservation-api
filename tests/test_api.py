import importlib
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('INVENTORY_DB', str(tmp_path/'test.sqlite3'))
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        yield c

def stock(c, n=5):
    assert c.put('/inventory', json={'sku':'widget','stock':n}).status_code == 200

def reserve(c, key='a', qty=2):
    return c.post('/reservations', json={'sku':'widget','quantity':qty}, headers={'Idempotency-Key':key})

def test_stock_and_availability(client):
    stock(client)
    assert client.get('/inventory/widget').json()['available'] == 5
    assert client.get('/inventory/missing').status_code == 404

def test_reservation_holds_and_releases(client):
    stock(client)
    rid=reserve(client).json()['id']
    assert client.get('/inventory/widget').json()['available']==3
    assert client.post(f'/reservations/{rid}/release').status_code==200
    assert client.get('/inventory/widget').json()['available']==5

def test_idempotency(client):
    stock(client)
    a=reserve(client).json()
    assert reserve(client).json()['id']==a['id']
    assert reserve(client, qty=3).status_code==409
    assert client.get('/inventory/widget').json()['available']==3

def test_oversell_rejected(client):
    stock(client, 1)
    assert reserve(client, qty=2).status_code==409
    assert client.get('/inventory/widget').json()['available']==1

def test_confirm_deducts_stock_once(client):
    stock(client)
    rid=reserve(client).json()['id']
    assert client.post(f'/reservations/{rid}/confirm').status_code==200
    assert client.post(f'/reservations/{rid}/confirm').status_code==200
    assert client.get('/inventory/widget').json()['available']==3
    assert client.post(f'/reservations/{rid}/release').status_code==409

def test_stock_cannot_underflow_active_holds(client):
    stock(client)
    reserve(client,qty=4)
    assert client.put('/inventory',json={'sku':'widget','stock':3}).status_code==409

def test_bad_input(client):
    assert client.put('/inventory',json={'sku':'x y','stock':-1}).status_code==422
    assert client.post('/reservations',json={'sku':'x','quantity':1}).status_code==422

def test_expiry(client, monkeypatch):
    import app.main as m
    stock(client)
    initial=int(m.time.time())
    monkeypatch.setattr(m.time,'time',lambda: initial)
    r=client.post('/reservations',json={'sku':'widget','quantity':4,'ttl_seconds':1},headers={'Idempotency-Key':'exp'})
    assert r.status_code==201
    monkeypatch.setattr(m.time,'time',lambda: initial+2)
    assert client.get('/inventory/widget').json()['available']==5
    assert client.post(f"/reservations/{r.json()['id']}/confirm").status_code==409

def test_unknown_reservation(client):
    assert client.post('/reservations/nope/confirm').status_code==404
    assert client.post('/reservations/nope/release').status_code==404

def test_released_idempotency(client):
    stock(client)
    rid=reserve(client).json()['id']
    client.post(f'/reservations/{rid}/release')
    assert client.post(f'/reservations/{rid}/release').status_code==200

def test_idempotency_rejects_different_ttl(client):
    stock(client)
    first = client.post('/reservations',
        json={'sku':'widget','quantity':2,'ttl_seconds':60},
        headers={'Idempotency-Key':'same-checkout'})
    assert first.status_code == 201
    replay = client.post('/reservations',
        json={'sku':'widget','quantity':2,'ttl_seconds':120},
        headers={'Idempotency-Key':'same-checkout'})
    assert replay.status_code == 409
    assert client.get('/inventory/widget').json()['available'] == 3


def test_idempotency_same_ttl_replays_original(client):
    stock(client)
    payload = {'sku':'widget','quantity':2,'ttl_seconds':60}
    headers = {'Idempotency-Key':'same-checkout'}
    first = client.post('/reservations', json=payload, headers=headers)
    second = client.post('/reservations', json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()


def test_invalid_reservation_sku_is_rejected(client):
    stock(client)
    response = client.post('/reservations',
        json={'sku':'../widget','quantity':1},
        headers={'Idempotency-Key':'invalid-sku'})
    assert response.status_code == 422


def test_expired_hold_cannot_be_released_as_active(client, monkeypatch):
    import app.main as m
    stock(client)
    now = int(m.time.time())
    monkeypatch.setattr(m.time, 'time', lambda: now)
    response = client.post('/reservations',
        json={'sku':'widget','quantity':2,'ttl_seconds':1},
        headers={'Idempotency-Key':'expire-release'})
    assert response.status_code == 201
    rid = response.json()['id']
    monkeypatch.setattr(m.time, 'time', lambda: now + 2)
    released = client.post(f'/reservations/{rid}/release')
    assert released.status_code == 409
    assert 'expired' in released.json()['detail']
    assert client.get('/inventory/widget').json()['available'] == 5
