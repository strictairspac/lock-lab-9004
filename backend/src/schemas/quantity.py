from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class BackfillInput(BaseModel):
    code: str = Field(min_length=2, max_length=80)
    quantity: int = Field(ge=0, le=100000)
    effective_at: datetime
    note: str = Field(default='', max_length=300)

    @field_validator('effective_at')
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError('生效时刻必须带时区')
        return value
