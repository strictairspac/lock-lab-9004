import os
import sys

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://locklab@127.0.0.1:17742/locklab_test')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')
os.environ.setdefault('SEED_DEMO', 'false')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import text

from src.config.database import Base, Session, engine
from src.models import entities
from src.models.entities import User
from src.services import quantity
from src.utils.security import password_hash


def _database_available():
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False


def _is_test_database():
    from src.config.settings import DATABASE_URL
    return DATABASE_URL.rstrip('/').endswith('_test')


def pytest_collection_modifyitems(config, items):
    """测试会 drop_all/create_all，只允许连名称以 _test 结尾的测试库。"""
    if not _database_available():
        for item in items:
            item.add_marker(pytest.mark.skip(reason='需要真实 PostgreSQL（DATABASE_URL）'))
        return
    if not _is_test_database():
        raise pytest.UsageError('DATABASE_URL 必须指向名称以 _test 结尾的测试库，拒绝清库')


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    quantity.ensure_schema(engine)
    with Session() as session:
        for role in ('admin', 'operator', 'viewer'):
            session.add(User(email=role + '@qingyu.test', password=password_hash('Demo2026!'), role=role))
        session.commit()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient
    from src.main import app
    return TestClient(app)


def login(client, role='operator'):
    response = client.post('/auth/login', json={'email': role + '@qingyu.test', 'password': 'Demo2026!'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def user(role='operator'):
    with Session() as session:
        return session.query(User).filter(User.email == role + '@qingyu.test').one()


def create_sample(client, headers, code='QY-T-001', quantity_value=100):
    response = client.post('/samples', headers=headers, json={
        'code': code, 'title': '测试留样', 'quantity': quantity_value, 'location': '冷藏间 T-01',
    })
    assert response.status_code == 201, response.text
    return response.json()


def create_transfer(client, headers, sample_id, code='MOVE-T-001', quantity_value=30, approved=None):
    payload = {'code': code, 'sample_id': sample_id, 'destination': '理化检测室',
               'quantity': quantity_value, 'note': '测试'}
    if approved is not None:
        payload['approved_quantity'] = approved
    return client.post('/transfers', headers=headers, json=payload)
