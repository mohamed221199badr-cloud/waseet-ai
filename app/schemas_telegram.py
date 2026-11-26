import uuid
from datetime import datetime

from pydantic import BaseModel


class TelegramLinkRead(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    created_at: datetime
    is_owner: bool = False
