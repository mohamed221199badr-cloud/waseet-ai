"""Notification outbox (Phase 3b, v3 C1/C3).

build_dedupe_key is the ONLY place dedupe keys are made:
    {event}:{order_id}:{recipient_type}:{recipient_id}:{channel}:{occurrence}
occurrence is the order_status_history.id that caused the event, or 0 for events
that cannot repeat - without it a second order.ready after redelivery would be
silently dropped as a duplicate (C1). emit() relies on UNIQUE(dedupe_key): two
racing dispatchers build the same key and the second insert is a no-op.
"""

import uuid

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models_orders import NotificationOutbox

CHANNEL_TELEGRAM = "telegram"


def build_dedupe_key(event: str, order_id: uuid.UUID, recipient_type: str,
                     recipient_id: str, channel: str,
                     occurrence: uuid.UUID | None) -> str:
    occ = str(occurrence) if occurrence is not None else "0"
    return f"{event}:{order_id}:{recipient_type}:{recipient_id}:{channel}:{occ}"


def emit(db: Session, business_id: uuid.UUID, event: str, order_id: uuid.UUID,
         recipient_type: str, recipient_id: str, *,
         channel: str = CHANNEL_TELEGRAM,
         occurrence: uuid.UUID | None = None,
         payload: dict | None = None) -> bool:
    """Queue one notification. True when a new row was queued; False when the same
    dedupe key already existed (redelivery, racing dispatcher) and nothing was added.
    The caller commits with the rest of its transaction, so a notification never
    outlives the change that caused it."""
    key = build_dedupe_key(event, order_id, recipient_type, recipient_id,
                           channel, occurrence)
    row = pg_insert(NotificationOutbox).values(
        id=uuid.uuid4(), business_id=business_id, dedupe_key=key, event=event,
        order_id=order_id, recipient_type=recipient_type,
        recipient_id=str(recipient_id), channel=channel, occurrence=occurrence,
        payload=payload or {}, status="pending", attempts=0,
    ).on_conflict_do_nothing(constraint="notification_outbox_dedupe_key_key")
    return db.execute(row).rowcount == 1
