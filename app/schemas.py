"""Pydantic schemas (request/response)."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=300)
    unit: str = Field(default="piece", max_length=30)
    price: Decimal = Field(ge=0)
    stock_quantity: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=30)
    price: Decimal | None = Field(default=None, ge=0)
    stock_quantity: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    sku: str
    name: str
    unit: str
    price: Decimal
    stock_quantity: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=300)


class AliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alias: str
    product_id: uuid.UUID


class BusinessRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
