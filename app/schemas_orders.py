"""Pydantic schemas for Milestone 2 (customers, prices, inventory, orders)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=3, max_length=30)
    address: str | None = Field(default=None, max_length=500)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, min_length=3, max_length=30)
    address: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    name: str
    phone: str | None
    address: str | None
    is_active: bool
    created_at: datetime


class CustomerPriceSet(BaseModel):
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class CustomerPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_id: uuid.UUID
    product_id: uuid.UUID
    price: Decimal


class StockAdjust(BaseModel):
    delta: int = Field(description="موجب = إضافة مخزون، سالب = خصم")
    reason: Literal["restock", "adjustment"] = "adjustment"
    note: str | None = Field(default=None, max_length=300)


class MovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: uuid.UUID
    delta: int
    balance_after: int
    reason: str
    order_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class StockRead(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    stock_quantity: int


class ItemIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)


class OrderCreate(BaseModel):
    customer_id: uuid.UUID
    channel: Literal["manual", "whatsapp", "telegram", "web"] = "manual"
    source_text: str | None = Field(default=None, max_length=5000)
    notes: str | None = Field(default=None, max_length=500)
    items: list[ItemIn] = Field(default_factory=list)


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    price_source: str
    line_total: Decimal


class CandidateRead(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    score: float
    kind: str


class ExceptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_text: str
    reason: str
    reason_detail: str | None
    customer_message: str | None
    quantity: int | None
    candidates: list[dict]
    status: str
    resolution_note: str | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    customer_id: uuid.UUID
    status: str
    channel: str
    source_text: str | None
    notes: str | None
    rejection_reason: str | None
    total: Decimal
    created_at: datetime
    decided_at: datetime | None
    items: list[ItemRead]
    exceptions: list[ExceptionRead] = []


class IntakeRequest(BaseModel):
    """Order text from any channel. The channel only labels where it came from."""

    text: str = Field(min_length=1, max_length=5000)
    customer_id: uuid.UUID | None = None
    customer_phone: str | None = Field(default=None, min_length=3, max_length=30)
    channel: Literal["manual", "whatsapp", "telegram", "web"] = "manual"


class LineResult(BaseModel):
    raw: str
    status: Literal["matched", "exception"]
    reason: str | None
    product_id: uuid.UUID | None
    match_type: str | None
    confidence: float | None
    quantity: int | None
    candidates: list[CandidateRead]


class IntakeResult(BaseModel):
    order: OrderRead
    lines: list[LineResult]
    matched_count: int
    exception_count: int


class ResolveBody(BaseModel):
    product_id: uuid.UUID
    quantity: int | None = Field(default=None, gt=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=1000)


class DismissBody(BaseModel):
    """B3: a dismissal must say why, so the audit trail can tell "not an order line"
    from a mistake. The database CHECK enforces a non-empty note."""
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("note must not be blank")
        return v.strip()
