"""Owner-side web chat: lets the owner try the chat agent as one of their customers without
Telegram. Same agent logic as the Telegram path (app.chat.agent). Auth is the owner's JWT;
the customer must belong to the owner's business. The web session is keyed by a negative
chat id derived from the customer, so it can never collide with a real Telegram chat."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import get_business, get_current_user
from app.chat import agent
from app.db import get_db
from app.models import Business, User
from app.models_orders import ChatSession, TelegramLink
from app.repositories import customers as customers_repo

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatIn(BaseModel):
    customer_id: uuid.UUID
    text: str = Field(min_length=1, max_length=5000)


def _link(business, customer) -> TelegramLink:
    chat_id = -(customer.id.int % (2**62)) - 1
    return TelegramLink(business_id=business.id, customer_id=customer.id, chat_id=chat_id)


@router.post("/message")
def message(body: ChatIn, business: Business = Depends(get_business),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    customer = customers_repo.get_customer(db, business.id, body.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    reply = agent.handle_message(db, _link(business, customer), body.text.strip())
    db.commit()
    return {"reply": reply}


@router.post("/reset")
def reset(body: ChatIn, business: Business = Depends(get_business),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    customer = customers_repo.get_customer(db, business.id, body.customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.execute(delete(ChatSession).where(
        ChatSession.business_id == business.id,
        ChatSession.chat_id == _link(business, customer).chat_id))
    db.commit()
    return {"ok": True}
