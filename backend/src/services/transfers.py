from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Transfer,Sample
from src.services import quantity
from src.utils.serialization import record
from .common import save


def review(db,row,grouped=None):
    """一条申请的复核口径：原核准量快照 + 按数量流水还原的登记时数量。"""
    if grouped is None: grouped=quantity.records_for(db,{row.sample_id})
    item=record(row)
    item['approved_quantity_known']=row.approved_quantity is not None
    restored,source=quantity.snapshot_at(grouped.get(row.sample_id,[]),row.created_at)
    item['registered_quantity']=restored
    item['registered_quantity_source']=quantity.source(source)
    item['restored_matches_approved']=(
        row.approved_quantity is not None and restored is not None and row.approved_quantity==restored
    )
    return item


def _decorate(db,rows):
    grouped=quantity.records_for(db,{row.sample_id for row in rows}) if rows else {}
    return [review(db,row,grouped) for row in rows]


def listing(db): return _decorate(db,list(db.scalars(select(Transfer).order_by(Transfer.id))))


def detail(db,ident):
    row=db.get(Transfer,ident)
    if not row: raise HTTPException(404,'流转申请不存在')
    return review(db,row)


def create(db,data,user):
    # 登记与改量并发时，锁住样本行：校验请求里观察到的用量与库中当前量一致，
    # 再把该数量作为核准量快照与申请一起提交；不一致说明改量已抢先落库，拒绝以旧量受理。
    sample=db.execute(select(Sample).where(Sample.id==data.sample_id).with_for_update()).scalar_one_or_none()
    if not sample: raise HTTPException(404,'样本不存在')
    observed=data.approved_quantity
    if observed is not None and observed!=sample.quantity:
        raise HTTPException(409,'样本可用量已变更（当前 '+str(sample.quantity)+' 份），请按最新数量重新提交申请')
    if data.quantity>sample.quantity: raise HTTPException(422,'移交量超过样本可用量')
    now=quantity.utcnow()
    payload=data.model_dump()
    payload['approved_quantity']=sample.quantity
    obj=Transfer(**payload,created_at=now)
    save(db,obj,user,'创建流转申请 '+data.code+'，受理核准量 '+str(sample.quantity))
    # 申请与核准量已在同一事务提交；返回复核口径，登记时数量由流水在同一时刻还原。
    return review(db,obj)
