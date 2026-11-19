"""Multi-request contention checks against a real file-backed SQLite database."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import importlib
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('INVENTORY_DB', str(tmp_path / 'contention.sqlite3'))
    import app.main as module
    importlib.reload(module)
    with TestClient(module.app) as client:
        yield client


def concurrent_reserve(client, count, quantity=1, same_key=False):
    barrier = Barrier(count)
    def submit(i):
        barrier.wait(timeout=15)
        return client.post('/reservations', json={'sku':'sku1','quantity':quantity},
            headers={'Idempotency-Key': 'same-key' if same_key else f'order-{i}'})
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(submit, range(count)))


def test_competing_checkouts_never_oversell(api):
    assert api.put('/inventory', json={'sku':'sku1','stock':5}).status_code == 200
    responses = concurrent_reserve(api, count=20)
    assert sum(r.status_code == 201 for r in responses) == 5
    assert sum(r.status_code == 409 for r in responses) == 15
    assert api.get('/inventory/sku1').json()['available'] == 0
    report = api.get('/health/inventory').json()
    assert report['oversubscribed_count'] == 0
    assert report['items'][0]['held'] == 5


def test_concurrent_identical_retries_share_one_reservation(api):
    api.put('/inventory', json={'sku':'sku1','stock':10})
    responses = concurrent_reserve(api, count=12, quantity=2, same_key=True)
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()['id'] for r in responses}) == 1
    assert api.get('/inventory/sku1').json()['available'] == 8


def test_simultaneous_confirm_only_deducts_once(api):
    api.put('/inventory', json={'sku':'sku1','stock':7})
    hold = api.post('/reservations', json={'sku':'sku1','quantity':3}, headers={'Idempotency-Key':'confirm-1'})
    rid = hold.json()['id']
    barrier = Barrier(10)
    def confirm(_):
        barrier.wait(timeout=15)
        return api.post(f'/reservations/{rid}/confirm')
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(confirm, range(10)))
    assert all(r.status_code == 200 for r in responses)
    assert api.get('/inventory/sku1').json()['available'] == 4
    assert api.get('/health/inventory').json()['items'][0]['stock'] == 4


def test_concurrent_release_is_idempotent(api):
    api.put('/inventory', json={'sku':'sku1','stock':3})
    rid = api.post('/reservations', json={'sku':'sku1','quantity':2}, headers={'Idempotency-Key':'release-1'}).json()['id']
    barrier = Barrier(8)
    def release(_):
        barrier.wait(timeout=15)
        return api.post(f'/reservations/{rid}/release')
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(release, range(8)))
    assert all(r.status_code == 200 for r in responses)
    assert api.get('/inventory/sku1').json()['available'] == 3
