"""Dashboard v2: the orders the Telegram bot actually creates (order_lines), plus a
daily summary, a customer list and confirm/reject for orders waiting on the cashier.

Read-mostly. Kitchen-type roles (no confirm and no report permission) get the same orders
without prices, totals or customer phone, matching the bot's kitchen notice."""

import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_business, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business, User
from app.models_orders import Customer, Order, OrderLine
from app.permissions import Permission as P
from app.permissions import permissions_for_role

router = APIRouter(prefix="/board", tags=["board"])
CAIRO = ZoneInfo("Africa/Cairo")
MAX_ORDERS = 200


class LineOut(BaseModel):
    item: str
    variant: str
    quantity: str
    unit: str | None
    unit_price: str | None
    line_total: str | None


class OrderOut(BaseModel):
    id: str
    short_id: str
    status: str
    created_at: str          # Cairo local, "YYYY-MM-DD HH:MM"
    method: str              # delivery | pickup
    address: str
    customer_name: str
    customer_phone: str | None
    total: str | None
    lines: list[LineOut]
    rejection_reason: str | None


class CustomerOut(BaseModel):
    id: str
    name: str
    phone: str | None
    address: str | None
    orders: int
    spent: str
    last_order: str | None


class RejectIn(BaseModel):
    reason: str | None = None


def _can_see_money(user: User) -> bool:
    held = permissions_for_role(user.role)
    return P.ORDER_CONFIRM in held or P.REPORT_VIEW in held


def _num(d) -> str:
    """Quantities and money without exponent notation or trailing zeros."""
    s = format(Decimal(d).normalize(), "f")
    return s


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:  # the DB stores naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CAIRO)


def _day_range(day: str) -> tuple[datetime | None, datetime | None]:
    """Cairo-day bounds as naive UTC (how the DB stores times)."""
    if day == "all":
        return None, None
    today = datetime.now(CAIRO).date()
    d = today - timedelta(days=1) if day == "yesterday" else today
    start = datetime.combine(d, time.min, tzinfo=CAIRO)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).replace(tzinfo=None),
            end.astimezone(timezone.utc).replace(tzinfo=None))


def _method(notes: str | None) -> tuple[str, str]:
    notes = notes or ""
    if notes.startswith("دليفري"):
        return "delivery", notes.split(" - ", 1)[1] if " - " in notes else ""
    return "pickup", ""


def _orders(db: Session, business_id: uuid.UUID, day: str, status: str | None):
    start, end = _day_range(day)
    q = select(Order).where(
        Order.business_id == business_id,
        Order.id.in_(select(OrderLine.order_id).where(OrderLine.business_id == business_id)))
    if status:
        q = q.where(Order.status == status)
    if start is not None:
        q = q.where(Order.created_at >= start, Order.created_at < end)
    return list(db.scalars(q.order_by(Order.created_at.desc()).limit(MAX_ORDERS)))


def _lines_by_order(db: Session, business_id: uuid.UUID, ids: list[uuid.UUID]):
    out: dict[uuid.UUID, list[OrderLine]] = {}
    if not ids:
        return out
    for ln in db.scalars(select(OrderLine).where(
            OrderLine.business_id == business_id, OrderLine.order_id.in_(ids))
            .order_by(OrderLine.created_at, OrderLine.id)):
        out.setdefault(ln.order_id, []).append(ln)
    return out


def _order_out(o: Order, lines: list[OrderLine], customer: Customer | None,
               money: bool) -> OrderOut:
    method, address = _method(o.notes)
    return OrderOut(
        id=str(o.id), short_id=str(o.id)[:8], status=o.status,
        created_at=_local(o.created_at).strftime("%Y-%m-%d %H:%M"),
        method=method, address=address if money else "",
        customer_name=customer.name if customer else "-",
        customer_phone=(customer.phone if customer and money else None),
        total=_num(o.total) if money else None,
        lines=[LineOut(item=ln.item_name, variant=ln.variant_name,
                       quantity=_num(ln.quantity), unit=ln.unit_label,
                       unit_price=_num(ln.unit_price) if money else None,
                       line_total=_num(ln.line_total) if money else None) for ln in lines],
        rejection_reason=o.rejection_reason if money else None)


@router.get("/orders", response_model=list[OrderOut])
def board_orders(
    day: str = Query("today", pattern="^(today|yesterday|all)$"),
    status: str | None = Query(None, pattern="^(pending|confirmed|rejected)$"),
    user: User = Depends(require(P.ORDER_VIEW_ALL)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    orders = _orders(db, business.id, day, status)
    lines = _lines_by_order(db, business.id, [o.id for o in orders])
    customers = {c.id: c for c in db.scalars(select(Customer).where(
        Customer.business_id == business.id,
        Customer.id.in_({o.customer_id for o in orders})))} if orders else {}
    money = _can_see_money(user)
    return [_order_out(o, lines.get(o.id, []), customers.get(o.customer_id), money)
            for o in orders]


@router.get("/summary")
def board_summary(
    day: str = Query("today", pattern="^(today|yesterday|all)$"),
    _: User = Depends(require(P.REPORT_VIEW)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    """Confirmed orders only: count, revenue, pickup/delivery split, top items."""
    orders = _orders(db, business.id, day, "confirmed")
    lines = _lines_by_order(db, business.id, [o.id for o in orders])
    pending = len(_orders(db, business.id, day, "pending"))
    by_method = {"pickup": {"orders": 0, "total": Decimal(0)},
                 "delivery": {"orders": 0, "total": Decimal(0)}}
    items: dict[str, dict] = {}
    for o in orders:
        m = _method(o.notes)[0]
        by_method[m]["orders"] += 1
        by_method[m]["total"] += o.total
        for ln in lines.get(o.id, []):
            row = items.setdefault(ln.item_name, {"item": ln.item_name, "qty": Decimal(0),
                                                  "total": Decimal(0)})
            row["qty"] += ln.quantity
            row["total"] += ln.line_total
    top = sorted(items.values(), key=lambda r: r["total"], reverse=True)[:10]
    revenue = sum((o.total for o in orders), Decimal(0))
    return {
        "day": day, "orders": len(orders), "pending": pending, "revenue": _num(revenue),
        "average": _num(revenue / len(orders)) if orders else "0",
        "by_method": {k: {"orders": v["orders"], "total": _num(v["total"])}
                      for k, v in by_method.items()},
        "top_items": [{"item": r["item"], "qty": _num(r["qty"]), "total": _num(r["total"])}
                      for r in top],
    }


@router.get("/customers", response_model=list[CustomerOut])
def board_customers(
    _: User = Depends(require(P.CRM_VIEW)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    stats = {r.customer_id: r for r in db.execute(
        select(Order.customer_id, func.count().label("n"),
               func.coalesce(func.sum(Order.total).filter(Order.status == "confirmed"), 0)
               .label("spent"), func.max(Order.created_at).label("last"))
        .where(Order.business_id == business.id, Order.status != "draft")
        .group_by(Order.customer_id))}
    out = []
    for c in db.scalars(select(Customer).where(Customer.business_id == business.id)
                        .order_by(Customer.name)):
        s = stats.get(c.id)
        out.append(CustomerOut(
            id=str(c.id), name=c.name, phone=c.phone, address=c.address,
            orders=s.n if s else 0, spent=_num(s.spent) if s else "0",
            last_order=_local(s.last).strftime("%Y-%m-%d %H:%M") if s and s.last else None))
    return out


def _decide(db: Session, business: Business, user: User, order_id: uuid.UUID,
            new_status: str, reason: str | None) -> dict:
    order = db.scalar(select(Order).where(
        Order.business_id == business.id, Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="الطلب مش في انتظار الاعتماد")
    order.status = new_status
    order.decided_at = datetime.now(timezone.utc)
    if new_status == "rejected":
        order.rejection_reason = (reason or "").strip()[:300] or None
    write_audit(db, business_id=business.id, actor_id=user.id, entity="order",
                entity_id=str(order.id),
                action="confirm" if new_status == "confirmed" else "reject",
                payload={"via": "dashboard", "by": str(user.id)})
    db.commit()
    return {"id": str(order.id), "status": order.status}


@router.post("/orders/{order_id}/confirm")
def board_confirm(order_id: uuid.UUID, user: User = Depends(require(P.ORDER_CONFIRM)),
                  business: Business = Depends(get_business), db: Session = Depends(get_db)):
    return _decide(db, business, user, order_id, "confirmed", None)


@router.post("/orders/{order_id}/reject")
def board_reject(order_id: uuid.UUID, body: RejectIn | None = None,
                 user: User = Depends(require(P.ORDER_REJECT)),
                 business: Business = Depends(get_business), db: Session = Depends(get_db)):
    return _decide(db, business, user, order_id, "rejected", body.reason if body else None)

