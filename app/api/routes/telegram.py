"""Owner-side Telegram management: issue link codes, list and revoke linked chats."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_business, get_current_user, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business, User
from app.models_orders import Customer, TelegramLink
from app.permissions import Permission as P
from app.repositories import customers as customers_repo
from app.schemas_telegram import LinkCodeCreate, LinkCodeRead, TelegramLinkRead
from app.telegram.service import new_link_code

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/link-codes", response_model=LinkCodeRead, status_code=status.HTTP_201_CREATED)
def create_link_code(
    body: LinkCodeCreate,
    _: None = Depends(require(P.CRM_MANAGE)),
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    customer = customers_repo.get_customer(db, business.id, body.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.is_active:
        raise HTTPException(status_code=409, detail="Customer is inactive")
    code, row = new_link_code(db, business.id, customer.id)
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="telegram_link_code",
        entity_id=str(row.id), action="create", payload={"customer_id": str(customer.id)},
    )
    db.commit()
    return LinkCodeRead(
        code=code, expires_at=row.expires_at,
        instruction=f"العميل يبعت للبوت: /start {code}",
    )


@router.get("/links", response_model=list[TelegramLinkRead])
def list_links(
    _: None = Depends(require(P.CRM_VIEW)),
    business: Business = Depends(get_business), db: Session = Depends(get_db)
):
    rows = db.execute(
        select(TelegramLink, Customer.name)
        .join(Customer, (Customer.business_id == TelegramLink.business_id)
              & (Customer.id == TelegramLink.customer_id))
        .where(TelegramLink.business_id == business.id)
        .order_by(TelegramLink.created_at.desc())
    )
    return [
        TelegramLinkRead(id=link.id, customer_id=link.customer_id, customer_name=name,
                         created_at=link.created_at)
        for link, name in rows
    ]


@router.post("/links/{link_id}/owner", status_code=status.HTTP_204_NO_CONTENT)
def mark_link_owner(
    link_id: uuid.UUID,
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a telegram link as the shop owner's own chat. Owner role only, and only
    through this authenticated API - never from a customer chat message (19673006).
    Owner chats are the only ones that may set the official menu photo."""
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    link = db.scalar(
        select(TelegramLink).where(
            TelegramLink.business_id == business.id, TelegramLink.id == link_id
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    link.is_owner = True
    db.add(link)
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="telegram_link",
        entity_id=str(link.customer_id), action="mark_owner",
        payload={"link_id": str(link.id), "by": str(user.id)},
    )
    db.commit()


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_link(
    link_id: uuid.UUID,
    _: None = Depends(require(P.CRM_MANAGE)),
    business: Business = Depends(get_business),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    link = db.scalar(
        select(TelegramLink).where(
            TelegramLink.business_id == business.id, TelegramLink.id == link_id
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    write_audit(
        db, business_id=business.id, actor_id=user.id, entity="telegram_link",
        entity_id=str(link.customer_id), action="delete", payload={"by": "owner"},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
