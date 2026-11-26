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
from app.schemas_telegram import TelegramLinkRead

router = APIRouter(prefix="/telegram", tags=["telegram"])


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
                         created_at=link.created_at, is_owner=link.is_owner)
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
