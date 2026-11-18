"""样本数量流水：受理快照之外的还原依据。

- baseline：样本登记时的基线数量。
- change：日常改量，生效时刻为保存时刻。
- backfill：补记过去生效的数量，只影响 [生效时刻, 下一次记录) 这段区间。

申请上的 approved_quantity 是受理瞬间的快照，流水不会回写它；复核时另外按流水
还原“登记时数量”，两者同时展示。
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Audit, Sample, SampleQuantityRecord
from src.utils.serialization import record

KIND_LABELS = {'baseline': '登记基线', 'change': '日常改量', 'backfill': '历史补记'}


def utcnow():
    return datetime.now(timezone.utc)


def normalize_time(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ensure_schema(engine):
    """create_all 只建新表，不会给既有库补列；这里做幂等的增量 DDL。"""
    statements = [
        'ALTER TABLE samples ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE',
        'ALTER TABLE transfers ADD COLUMN IF NOT EXISTS approved_quantity INTEGER',
        'ALTER TABLE transfers ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE',
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def bootstrap_baselines(db):
    """给没有任何流水的既有样本补一条当前基线。

    历史样本的真实登记时刻不可考，因此基线生效时刻取补齐时刻；这不会伪造
    旧申请的数据：旧申请的核准量仍为 NULL，页面仍标为未知。
    """
    now = utcnow()
    missing = db.scalars(
        select(Sample).where(
            ~select(SampleQuantityRecord.id)
            .where(SampleQuantityRecord.sample_id == Sample.id)
            .exists()
        )
    ).all()
    for sample in missing:
        if sample.created_at is None:
            sample.created_at = now
        db.add(SampleQuantityRecord(
            sample_id=sample.id, quantity=sample.quantity,
            effective_at=normalize_time(sample.created_at), kind='baseline',
            note='系统补齐历史基线', actor='system', created_at=now,
        ))
    if missing:
        db.commit()
    return len(missing)


def records_for(db, sample_ids=None):
    query = select(SampleQuantityRecord).order_by(
        SampleQuantityRecord.sample_id, SampleQuantityRecord.effective_at, SampleQuantityRecord.id
    )
    if sample_ids is not None:
        query = query.where(SampleQuantityRecord.sample_id.in_(list(sample_ids)))
    grouped = {}
    for item in db.scalars(query):
        grouped.setdefault(item.sample_id, []).append(item)
    return grouped


def snapshot_at(records, when):
    """取目标时刻生效的数量，返回 (数量, 记录)。

    规则是最直白的时间线回放：取 effective_at <= 目标时刻的最后一条记录（同一时刻
    按自增 id 决胜）。基线也是时间线上的一条记录，因此一条早于基线的补记只影响
    它自己到基线之间的区间，不会改变基线之后的还原结果——这正是“补记只影响生效
    时刻到下一次改量”。
    """
    when = normalize_time(when)
    if when is None:
        return None, None
    best = None
    best_key = None
    for item in records:
        effective = normalize_time(item.effective_at)
        if effective <= when:
            key = (effective, item.id)
            if best_key is None or key > best_key:
                best, best_key = item, key
    return (best.quantity, best) if best else (None, None)


def source(item):
    if item is None:
        return None
    return {
        'record_id': item.id, 'kind': item.kind, 'kind_label': KIND_LABELS.get(item.kind, item.kind),
        'code': item.backfill_code, 'effective_at': normalize_time(item.effective_at).isoformat(),
    }


def _same_content(item, sample_id, quantity, when, note):
    return (
        item.sample_id == sample_id
        and item.quantity == quantity
        and normalize_time(item.effective_at) == when
        and (item.note or '') == (note or '')
    )


def add_backfill(db, sample_id, data, user):
    """新增补记。返回 (记录, 是否为同编号幂等重放)。

    同一补记编号重试时不重复生效；编号相同但样本、数量、生效时刻或备注变化则拒绝。
    """
    sample = db.execute(
        select(Sample).where(Sample.id == sample_id).with_for_update()
    ).scalar_one_or_none()
    if not sample:
        raise HTTPException(404, '样本不存在')
    when = normalize_time(data.effective_at)
    existing = db.scalar(
        select(SampleQuantityRecord).where(SampleQuantityRecord.backfill_code == data.code)
    )
    if existing:
        if _same_content(existing, sample_id, data.quantity, when, data.note):
            return record(existing), True
        raise HTTPException(409, '补记编号已存在且内容不同，拒绝重复生效')
    if when > utcnow() + timedelta(seconds=5):
        raise HTTPException(422, '补记生效时刻不能晚于当前时间')
    now = utcnow()
    item = SampleQuantityRecord(
        sample_id=sample_id, quantity=data.quantity, effective_at=when, kind='backfill',
        backfill_code=data.code, note=data.note, actor=user.email, created_at=now,
    )
    db.add(item)
    db.add(Audit(actor=user.email, action='补记样本量 '+data.code+' 生效 '+when.isoformat()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(SampleQuantityRecord).where(SampleQuantityRecord.backfill_code == data.code)
        )
        if existing and _same_content(existing, sample_id, data.quantity, when, data.note):
            return record(existing), True
        if existing:
            raise HTTPException(409, '补记编号已存在且内容不同，拒绝重复生效')
        raise HTTPException(409, '补记编号已存在或引用对象无效，请调整后重试')
    return record(item), False
