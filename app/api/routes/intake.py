"""Order intake: text in, draft order out. Channel-independent.

The business owner (MVP: user confirmation only) still submits and confirms the draft
through the normal order endpoints. Nothing here confirms an order or changes stock.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_business, get_current_user, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business, User
from app.permissions import Permission as P
from app.repositories import customers as customers_repo
from app.repositories import intake as repo
from app.repositories import orders as orders_repo
from app.schemas_orders import (
    CandidateRead,
    DismissBody,
    IntakeRequest,
    IntakeResult,
    LineResult,
    OrderRead,
    ResolveBody,
)

router = APIRouter(tags=["intake"])


@router.post("/intake/text-order", response_model=IntakeResult, status_code=status.HTTP_201_CREATED)
def text_to_draft_order(
    body: IntakeRequest,
    _: None = Depends(require(P.ORDER_CREATE_MANUAL)),
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (body.customer_id is None) == (body.customer_phone is None):
        raise HTTPException(
            status_code=422, detail="Send exactly one of customer_id or customer_phone"
        )
    if body.customer_id is not None:
        customer = customers_repo.get_customer(db, business.id, body.customer_id)
    else:
        customer = repo.find_customer_by_phone(db, business.id, body.customer_phone)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    try:
        order, results = repo.create_draft_from_text(
            db, business.id, customer, body.text, body.channel
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not results:
        db.rollback()
        raise HTTPException(status_code=422, detail="No order lines found in the text")
    matched = sum(1 for r in results if r.status == "matched")
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="order", entity_id=str(order.id),
        action="create",
        payload={"source": "text_intake", "channel": body.channel, "lines": len(results),
                 "matched": matched, "exceptions": len(results) - matched},
    )
    db.commit()
    db.refresh(order)
    lines = [
        LineResult(
            raw=r.raw, status=r.status, reason=r.reason, product_id=r.product_id,
            match_type=r.match_type, confidence=r.confidence, quantity=r.quantity,
            candidates=[CandidateRead(**c.__dict__) for c in r.candidates],
        )
        for r in results
    ]
    return IntakeResult(order=OrderRead.model_validate(order), lines=lines, matched_count=matched,
                        exception_count=len(results) - matched)


def _open_exception_or_404(db, business_id, order_id, exception_id):
    order = orders_repo.get_order(db, business_id, order_id, lock=True)
    exc = repo.get_exception(db, business_id, order_id, exception_id) if order else None
    if order is None or exc is None:
        raise HTTPException(status_code=404, detail="Exception not found")
    if order.status != "draft" or exc.status != "open":
        raise HTTPException(status_code=409, detail="Only open exceptions of draft orders")
    return order, exc


@router.post("/orders/{order_id}/exceptions/{exception_id}/resolve", response_model=OrderRead)
def resolve_exception(
    order_id: uuid.UUID,
    exception_id: uuid.UUID,
    body: ResolveBody,
    _: None = Depends(require(P.ORDER_EDIT_LINES)),
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The owner picks the product (and quantity if the text had none)."""
    order, exc = _open_exception_or_404(db, business.id, order_id, exception_id)
    quantity = body.quantity or exc.quantity
    if quantity is None:
        raise HTTPException(status_code=422, detail="quantity is required for this line")
    try:
        item = orders_repo.add_item(db, order, body.product_id, quantity)
    except orders_repo.ProductUnavailable:
        db.rollback()
        raise HTTPException(status_code=404, detail="Product not found or inactive") from None
    except orders_repo.DuplicateItem:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product already in this order") from None
    repo.close_exception(exc, "resolved", resolved_by=user.id, resolution_note=body.note)
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="order_exception",
        entity_id=str(exc.id), action="update",
        payload={"status": "resolved", "order_id": str(order.id), "item_id": str(item.id),
                 "product_id": str(body.product_id), "quantity": quantity,
                 "note": body.note},
    )
    db.commit()
    db.refresh(order)
    return order


@router.post("/orders/{order_id}/exceptions/{exception_id}/dismiss", response_model=OrderRead)
def dismiss_exception(
    order_id: uuid.UUID,
    exception_id: uuid.UUID,
    body: DismissBody,
    _: None = Depends(require(P.ORDER_EDIT_LINES)),
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order, exc = _open_exception_or_404(db, business.id, order_id, exception_id)
    repo.close_exception(exc, "dismissed", resolved_by=user.id, resolution_note=body.note)
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="order_exception",
        entity_id=str(exc.id), action="update",
        payload={"status": "dismissed", "order_id": str(order.id), "note": body.note},
    )
    db.commit()
    db.refresh(order)
    return order
