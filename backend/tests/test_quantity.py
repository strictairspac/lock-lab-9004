from datetime import datetime, timedelta, timezone

from src.config.database import Session
from src.models.entities import Sample, SampleQuantityRecord, Transfer

from .conftest import create_sample, create_transfer, login


def test_new_transfer_snapshots_approved_quantity_and_restores_it(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=120)

    response = create_transfer(client, headers, sample['id'], quantity_value=30, approved=120)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['approved_quantity'] == 120
    assert body['approved_quantity_known'] is True
    assert body['registered_quantity'] == 120
    assert body['restored_matches_approved'] is True
    assert body['created_at']

    with Session() as db:
        assert db.get(Transfer, body['id']).approved_quantity == 120
        kinds = [row.kind for row in db.query(SampleQuantityRecord).filter_by(sample_id=sample['id'])]
        assert kinds == ['baseline']


def test_old_transfer_without_basis_is_unknown_and_not_guessed(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=60)
    with Session() as db:
        legacy = Transfer(code='OLD-001', sample_id=sample['id'], destination='理化检测室',
                          quantity=10, note='历史申请', approved_quantity=None, created_at=None)
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

    body = client.get('/transfers/%d' % legacy_id, headers=headers).json()
    assert body['approved_quantity'] is None
    assert body['approved_quantity_known'] is False
    assert body['registered_quantity'] is None
    assert body['restored_matches_approved'] is False

    listed = client.get('/transfers', headers=headers).json()
    row = next(item for item in listed if item['id'] == legacy_id)
    assert row['approved_quantity_known'] is False


def test_daily_change_updates_current_quantity_and_appends_change_record(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    response = client.put('/samples/%d' % sample['id'], headers=headers, json={
        'code': sample['code'], 'title': sample['title'], 'quantity': 90, 'location': sample['location'],
    })
    assert response.status_code == 200, response.text
    assert response.json()['quantity'] == 90

    timeline = client.get('/samples/%d/quantity' % sample['id'], headers=headers).json()
    assert [(row['kind'], row['quantity']) for row in timeline['records']] == [('baseline', 100), ('change', 90)]


def test_backfill_only_affects_interval_before_next_change(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=120)
    sid = sample['id']

    with Session() as db:
        obj = db.get(Sample, sid)
        # 手工构造一段历史：09:00 登记 100 → 10:00 日常改量 120。
        baseline = db.query(SampleQuantityRecord).filter_by(sample_id=sid, kind='baseline').one()
        baseline.quantity = 100
        baseline.effective_at = datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)
        db.add(SampleQuantityRecord(sample_id=sid, quantity=120, kind='change', note='10:00 改量',
                                    effective_at=datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc),
                                    actor='operator@qingyu.test'))
        obj.quantity = 120
        db.commit()

    # 09:30 受理的申请，核准量快照 100。
    with Session() as db:
        transfer = Transfer(code='MOVE-BF-01', sample_id=sid, destination='理化检测室', quantity=20,
                            note='', approved_quantity=100,
                            created_at=datetime(2026, 11, 17, 1, 30, tzinfo=timezone.utc))
        db.add(transfer)
        db.commit()
        tid = transfer.id

    # 09:15 的历史事实实际是 130，补记生效后只应影响 09:15 到 10:00 的区间。
    response = client.post('/samples/%d/quantity/backfill' % sid, headers=headers, json={
        'code': 'BF-001', 'quantity': 130,
        'effective_at': datetime(2026, 11, 17, 1, 15, tzinfo=timezone.utc).isoformat(),
        'note': '按纸质交接单补录',
    })
    assert response.status_code == 201, response.text
    assert response.json()['kind'] == 'backfill'

    detail = client.get('/transfers/%d' % tid, headers=headers).json()
    # 原核准量保持快照，不被补记改写。
    assert detail['approved_quantity'] == 100
    # 登记时刻 09:30 落在 [09:15, 10:00)，按补记值还原。
    assert detail['registered_quantity'] == 130
    assert detail['restored_matches_approved'] is False
    assert detail['registered_quantity_source']['code'] == 'BF-001'

    # 补记不改台账当前量和 10:00 之后的还原值。
    timeline = client.get('/samples/%d/quantity' % sid, headers=headers).json()
    assert timeline['sample']['quantity'] == 120
    from src.services.quantity import normalize_time, snapshot_at
    records = list(db_records(timeline))
    after_change, _ = snapshot_at(records, datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc))
    assert after_change == 120


def db_records(timeline):
    class Row:
        def __init__(self, payload):
            self.id = payload['id']
            self.quantity = payload['quantity']
            self.effective_at = payload['effective_at']
    return [Row(row) for row in timeline['records']]


def test_backfill_does_not_change_original_approved_quantity_even_when_later(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=80)
    sid = sample['id']
    # 把登记基线移到 1 小时前，留出可补记的过去区间。
    with Session() as db:
        baseline = db.query(SampleQuantityRecord).filter_by(sample_id=sid, kind='baseline').one()
        baseline.effective_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()

    created = create_transfer(client, headers, sid, quantity_value=10, approved=80).json()
    assert created['registered_quantity'] == 80

    # 补记落在基线之后、受理之前：还原量随之修正，但原核准量不动。
    response = client.post('/samples/%d/quantity/backfill' % sid, headers=headers, json={
        'code': 'BF-LATER', 'quantity': 95,
        'effective_at': (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
        'note': '补记受理前的数量',
    })
    assert response.status_code == 201, response.text

    detail = client.get('/transfers/%d' % created['id'], headers=headers).json()
    assert detail['approved_quantity'] == 80          # 原核准量不变
    assert detail['registered_quantity'] == 95        # 还原量随补记变化
    assert detail['restored_matches_approved'] is False

    # 台账当前量与补记无关。
    assert client.get('/samples/%d/quantity' % sid, headers=headers).json()['sample']['quantity'] == 80


def test_backfill_retry_same_code_is_idempotent_and_content_change_rejected(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']
    effective = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    payload = {'code': 'BF-RETRY', 'quantity': 77, 'effective_at': effective, 'note': '重试测试'}

    first = client.post('/samples/%d/quantity/backfill' % sid, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    second = client.post('/samples/%d/quantity/backfill' % sid, headers=headers, json=payload)
    assert second.status_code == 200, second.text
    assert second.headers.get('X-Idempotent-Replay') == 'true'
    assert second.json()['id'] == first.json()['id']

    with Session() as db:
        assert db.query(SampleQuantityRecord).filter_by(backfill_code='BF-RETRY').count() == 1

    changed = client.post('/samples/%d/quantity/backfill' % sid, headers=headers,
                          json={**payload, 'quantity': 78})
    assert changed.status_code == 409, changed.text
    changed_time = client.post('/samples/%d/quantity/backfill' % sid, headers=headers,
                               json={**payload, 'effective_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()})
    assert changed_time.status_code == 409, changed_time.text

    with Session() as db:
        assert db.query(SampleQuantityRecord).filter_by(backfill_code='BF-RETRY').count() == 1


def test_backfill_rejects_naive_or_future_effective_time(client):
    headers = login(client)
    sample = create_sample(client, headers, quantity_value=100)
    sid = sample['id']
    naive = client.post('/samples/%d/quantity/backfill' % sid, headers=headers,
                        json={'code': 'BF-NAIVE', 'quantity': 50, 'effective_at': '2026-11-16T10:00:00'})
    assert naive.status_code == 422, naive.text
    future = client.post('/samples/%d/quantity/backfill' % sid, headers=headers,
                         json={'code': 'BF-FUTURE', 'quantity': 50,
                               'effective_at': (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()})
    assert future.status_code == 422, future.text


def test_viewer_cannot_backfill(client):
    operator = login(client, 'operator')
    sample = create_sample(client, operator, quantity_value=100)
    viewer = login(client, 'viewer')
    response = client.post('/samples/%d/quantity/backfill' % sample['id'], headers=viewer, json={
        'code': 'BF-VIEWER', 'quantity': 50, 'effective_at': datetime.now(timezone.utc).isoformat(),
    })
    assert response.status_code == 403, response.text
