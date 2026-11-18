from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Sample,SampleQuantityRecord
from src.services import quantity
from .common import save
from src.utils.serialization import record


def listing(db): return [record(x) for x in db.scalars(select(Sample).order_by(Sample.id))]


def create(db,data,user):
    now=quantity.utcnow()
    obj=Sample(**data.model_dump(),created_at=now)
    try:
        db.add(obj)
        db.flush()
        db.add(SampleQuantityRecord(sample_id=obj.id,quantity=obj.quantity,effective_at=now,kind='baseline',note='样本登记',actor=user.email,created_at=now))
    except IntegrityError:
        db.rollback();raise HTTPException(409,'样本编号已存在，请调整后重试')
    return save(db,obj,user,'创建样本 '+data.code)


def update(db,ident,data,user):
    # 改量照常：锁住样本行后更新当前量，并追加一条日常改量流水（生效时刻为保存时刻）。
    obj=db.execute(select(Sample).where(Sample.id==ident).with_for_update()).scalar_one_or_none()
    if not obj: raise HTTPException(404,'样本不存在')
    previous=obj.quantity
    for k,v in data.model_dump().items(): setattr(obj,k,v)
    now=quantity.utcnow()
    if obj.quantity!=previous:
        db.add(SampleQuantityRecord(sample_id=obj.id,quantity=obj.quantity,effective_at=now,kind='change',note='日常改量 '+str(previous)+' → '+str(obj.quantity),actor=user.email,created_at=now))
        action='更新样本 '+data.code+'，改量 '+str(previous)+' → '+str(obj.quantity)
    else:
        action='更新样本 '+data.code
    return save(db,obj,user,action)


def timeline(db,ident):
    obj=db.get(Sample,ident)
    if not obj: raise HTTPException(404,'样本不存在')
    rows=[record(x) for x in db.scalars(select(SampleQuantityRecord).where(SampleQuantityRecord.sample_id==ident).order_by(SampleQuantityRecord.effective_at,SampleQuantityRecord.id))]
    return {'sample':record(obj),'records':rows}
