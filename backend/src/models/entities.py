from sqlalchemy import Column, Integer, String, JSON, DateTime, ForeignKey, Text, UniqueConstraint, CheckConstraint
from datetime import datetime, timezone
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True);email=Column(String(120),unique=True,nullable=False)
    password=Column(String(300),nullable=False);role=Column(String(20),nullable=False)
class Sample(Base):
    __tablename__='samples'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    title=Column(String(160),nullable=False);quantity=Column(Integer,nullable=False);location=Column(String(120),nullable=False)
    created_at=Column(DateTime(timezone=True),nullable=True)
class Transfer(Base):
    __tablename__='transfers'
    id=Column(Integer,primary_key=True);code=Column(String(80),unique=True,nullable=False)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False);destination=Column(String(120),nullable=False)
    quantity=Column(Integer,nullable=False);note=Column(String(300),default='')
    # 受理时快照：申请登记那一刻样本的核准量。历史申请没有依据，保持 NULL 并在页面标为“未知”。
    approved_quantity=Column(Integer,nullable=True)
    # 登记（受理）时刻。历史申请缺失时保持 NULL，无法按流水还原登记时数量。
    created_at=Column(DateTime(timezone=True),nullable=True)
class SampleQuantityRecord(Base):
    """样本数量流水：登记基线、日常改量、历史补记。

    还原某一时刻的数量时，取 effective_at <= 该时刻的最后一条记录；
    补记只改变从它生效时刻到下一次改量之间的还原结果，不会改写申请上的核准量快照。
    """
    __tablename__='sample_quantity_records'
    id=Column(Integer,primary_key=True)
    sample_id=Column(Integer,ForeignKey('samples.id'),nullable=False,index=True)
    quantity=Column(Integer,nullable=False)
    effective_at=Column(DateTime(timezone=True),nullable=False,index=True)
    kind=Column(String(20),nullable=False)  # baseline / change / backfill
    backfill_code=Column(String(80),nullable=True)
    note=Column(String(300),nullable=False,default='')
    actor=Column(String(120),nullable=False,default='')
    created_at=Column(DateTime(timezone=True),nullable=True)
    __table_args__=(
        UniqueConstraint('backfill_code',name='uq_sample_quantity_records_backfill_code'),
        CheckConstraint('quantity >= 0',name='ck_sample_quantity_records_nonnegative'),
    )
class Trial(Base):
    __tablename__='trials'
    id=Column(Integer,primary_key=True);name=Column(String(120),nullable=False)
    transfer_id=Column(Integer,ForeignKey('transfers.id'),nullable=False);timeout_ms=Column(Integer,nullable=False)
    status=Column(String(20),default='queued',nullable=False);result=Column(JSON,nullable=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc));error=Column(Text,default='')
class TrialBalance(Base):
    __tablename__='trial_balances'
    trial_id=Column(Integer,ForeignKey('trials.id'),primary_key=True);remaining=Column(Integer,nullable=False);received=Column(Integer,nullable=False,default=0)
class Receipt(Base):
    __tablename__='receipts'
    id=Column(Integer,primary_key=True);trial_id=Column(Integer,ForeignKey('trials.id'),nullable=False)
    actor=Column(String(20),nullable=False);quantity=Column(Integer,nullable=False)
    __table_args__=(UniqueConstraint('trial_id','actor'),)
class Audit(Base):
    __tablename__='audit_logs'
    id=Column(Integer,primary_key=True);actor=Column(String(120),nullable=False);action=Column(String(160),nullable=False)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
