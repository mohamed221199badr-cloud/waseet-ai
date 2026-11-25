import uuid
from datetime import datetime

from pydantic import BaseModel


class LinkCodeCreate(BaseModel):
    customer_id: uuid.UUID


class LinkCodeRead(BaseModel):
    code: str
    expires_at: datetime
    instruction: str


class TelegramLinkRead(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    created_at: datetime
