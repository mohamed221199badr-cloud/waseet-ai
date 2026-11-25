"""Audit trail writer. Called inside the same transaction as the write it records."""

import uuid

from sqlalchemy.orm import Session

from app.models import AuditEvent


def write_audit(
    db: Session,
    *,
    business_id: uuid.UUID,
    entity: str,
    entity_id: str,
    action: str,
    payload: dict,
    actor_id: uuid.UUID | None = None,
) -> None:
    db.add(
        AuditEvent(
            business_id=business_id,
            actor_id=actor_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            payload=payload,
        )
    )
