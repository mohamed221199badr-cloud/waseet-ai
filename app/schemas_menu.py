"""Pydantic schemas for the Phase 1 menu (data-entry only until the Phase 2 cutover)."""

import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sort_order: int = 0


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sort_order: int


class VariantIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_default: bool = False
    sort_order: int = 0


class VariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    price: Decimal | None
    is_default: bool
    sort_order: int


class ItemCreate(BaseModel):
    category: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    kind: Literal["food", "service"] = "food"
    sold_by: Literal["unit", "weight"]
    unit_label: str | None = Field(default=None, max_length=30)
    min_quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int = 0
    variants: list[VariantIn] = Field(min_length=1)
    aliases: list[str] = []

    @model_validator(mode="after")
    def _weight_needs_a_unit_label(self):
        if self.sold_by == "weight" and not (self.unit_label or "").strip():
            raise ValueError("unit_label is required when sold_by is weight")
        return self


class ItemUpdate(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    kind: Literal["food", "service"] | None = None
    sold_by: Literal["unit", "weight"] | None = None
    unit_label: str | None = Field(default=None, max_length=30)
    min_quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    name: str
    kind: str
    sold_by: str
    unit_label: str | None
    min_quantity: Decimal | None
    description: str | None
    sort_order: int
    is_available: bool
    variants: list[VariantRead]
    aliases: list["AliasRead"]


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=300)


class AliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alias: str


class AvailabilityIn(BaseModel):
    is_available: bool


class VariantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    sort_order: int | None = None


ItemRead.model_rebuild()
