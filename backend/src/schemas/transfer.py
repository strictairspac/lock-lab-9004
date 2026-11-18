from pydantic import BaseModel, Field
class TransferInput(BaseModel):
    code: str=Field(min_length=2,max_length=80)
    sample_id: int=Field(gt=0)
    destination: str=Field(min_length=2,max_length=120)
    quantity: int=Field(ge=1,le=100000)
    note: str=Field(default='',max_length=300)
    # 登记表单提交时用户看到的样本可用量。提供时与库中当前量比对，防止用旧量受理。
    approved_quantity: int|None=Field(default=None,ge=0,le=100000)
