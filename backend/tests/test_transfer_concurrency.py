"""登记与改量并发：用两条真实连接同时提交，验证行锁下的受理口径。"""
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from src.config.database import Session
from src.models.entities import Audit, Sample, SampleQuantityRecord, Transfer

from .conftest import create_sample, create_transfer, login


def run_concurrently(*calls):
    results = [None] * len(calls)
    barrier = threading.Barrier(len(calls))

    def worker(index, call):
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=worker, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert all(not thread.is_alive() for thread in threads), '并发线程未在期限内结束'
    return results


def test_concurrent_transfer_and_quantity_change_keeps_consistent_snapshot(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']

    def register():
        return create_transfer(client, headers, sid, code='MOVE-RACE', quantity_value=30, approved=100)

    def change():
        return client.put('/samples/%d' % sid, headers=headers, json={
            'code': sample['code'], 'title': sample['title'], 'quantity': 90, 'location': sample['location'],
        })

    register_response, change_response = run_concurrently(register, change)
    assert change_response.status_code == 200, change_response.text
    assert register_response.status_code in (201, 409), register_response.text

    with Session() as db:
        current = db.get(Sample, sid).quantity
        transfers = db.query(Transfer).filter_by(code='MOVE-RACE').all()

    if register_response.status_code == 409:
        # 改量先落库：登记被拒，不得以旧量受理。
        assert transfers == []
        assert current == 90
        return

    body = register_response.json()
    assert current == 90
    # 登记先拿到锁时，受理量是那一刻的 100；改量随后才生效。申请与快照同事务落库。
    assert body['approved_quantity'] == 100
    with Session() as db:
        saved = db.query(Transfer).filter_by(code='MOVE-RACE').one()
        assert saved.approved_quantity == 100


def test_concurrent_transfer_with_stale_observed_quantity_is_rejected(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']

    # 用户打开表单时看到 100；期间改量已落库为 90，再提交旧表单。
    response = client.put('/samples/%d' % sid, headers=headers, json={
        'code': sample['code'], 'title': sample['title'], 'quantity': 90, 'location': sample['location'],
    })
    assert response.status_code == 200, response.text

    stale = create_transfer(client, headers, sid, code='MOVE-STALE', quantity_value=30, approved=100)
    assert stale.status_code == 409, stale.text
    assert '样本可用量已变更' in stale.json()['detail']

    with Session() as db:
        assert db.query(Transfer).filter_by(code='MOVE-STALE').count() == 0

    fresh = create_transfer(client, headers, sid, code='MOVE-FRESH', quantity_value=30, approved=90)
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()['approved_quantity'] == 90


def test_concurrent_duplicate_transfer_code_only_one_wins(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']

    first = lambda: create_transfer(client, headers, sid, code='MOVE-DUP', quantity_value=10, approved=100)
    second = lambda: create_transfer(client, headers, sid, code='MOVE-DUP', quantity_value=10, approved=100)
    results = run_concurrently(first, second)
    codes = sorted(response.status_code for response in results)
    assert codes == [201, 409], [r.text for r in results]

    with Session() as db:
        assert db.query(Transfer).filter_by(code='MOVE-DUP').count() == 1


def test_transfer_holds_row_lock_so_change_waits(client):
    """登记持锁期间，改量必须等待，不能穿插写入造成核准量偏差。"""
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']

    locked = threading.Event()
    release = threading.Event()
    change_result = {}

    from src.config.database import engine

    def long_registration():
        # 模拟登记事务：锁样本行，确认后提交。
        with engine.begin() as conn:
            conn.execute(select(Sample.id).where(Sample.id == sid).with_for_update())
            locked.set()
            assert release.wait(10)
        return None

    def change():
        started = time.perf_counter()
        try:
            change_result['response'] = client.put('/samples/%d' % sid, headers=headers, json={
                'code': sample['code'], 'title': sample['title'], 'quantity': 90, 'location': sample['location'],
            })
        except Exception as error:  # pragma: no cover - 失败时把线程异常带回主线程
            change_result['error'] = error
            raise
        finally:
            change_result['elapsed'] = time.perf_counter() - started
        return change_result['response']

    holder = threading.Thread(target=long_registration)
    holder.start()
    assert locked.wait(5)
    changer = threading.Thread(target=change)
    changer.start()
    time.sleep(0.3)
    assert changer.is_alive(), '改量未等待样本行锁'
    release.set()
    holder.join(10)
    changer.join(10)
    assert change_result['elapsed'] >= 0.25
    assert change_result['response'].status_code == 200
