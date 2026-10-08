
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    DeviceStatus,
    Junction,
)
from backend.app.infrastructure.database.session import get_db


router = APIRouter(
    prefix="/api/junctions",
    tags=["Junction Monitoring"],
)


def find_junction(db: Session, junction_id: str) -> Junction:
    junction = db.get(Junction, junction_id)

    if junction is None:
        raise HTTPException(
            status_code=404,
            detail="Junction not found.",
        )

    return junction


@router.get("/{junction_id}/history")
def get_junction_history(
    junction_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Return newest audit entries with pagination."""

    find_junction(db, junction_id)

    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.junction_id == junction_id)
        .order_by(AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "junction_id": junction_id,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": log.id,
                "event_type": log.event_type,
                "description": log.description,
                "details": log.details,
                "timestamp": log.created_at.isoformat() + "Z",
            }
            for log in logs
        ],
    }


@router.get("/{junction_id}/devices")
def get_junction_devices(
    junction_id: str,
    db: Session = Depends(get_db),
):
    """Return the latest recorded health for registered devices."""

    find_junction(db, junction_id)

    devices = db.scalars(
        select(DeviceStatus)
        .where(DeviceStatus.junction_id == junction_id)
        .order_by(DeviceStatus.device_type, DeviceStatus.direction)
    ).all()

    return {
        "junction_id": junction_id,
        "devices": [
            {
                "device_type": device.device_type,
                "direction": device.direction,
                "status": device.status,
                "last_seen_at": (
                    device.last_seen_at.isoformat() + "Z"
                    if device.last_seen_at is not None
                    else None
                ),
            }
            for device in devices
        ],
    }


@router.get("/{junction_id}")
def get_junction_details(
    junction_id: str,
    db: Session = Depends(get_db),
):
    """Return the persisted operating configuration of a junction."""

    junction = find_junction(db, junction_id)

    return {
        "junction_id": junction.id,
        "name": junction.name,
        "mode": junction.mode,
        "active_phase": junction.active_phase,
        "target_phase": junction.target_phase,
        "manual_phase": junction.manual_phase,
        "transition_stage": junction.transition_stage,
        "controller_status": junction.controller_status,
        "phase_started_at": (
            junction.phase_started_at.isoformat() + "Z"
            if junction.phase_started_at else None
        ),
        "transition_due_at": (
            junction.transition_due_at.isoformat() + "Z"
            if junction.transition_due_at else None
        ),
    }
