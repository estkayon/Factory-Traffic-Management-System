
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.app.main import app
from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    Junction,
    SensorEvent,
)
from backend.app.infrastructure.database.session import get_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test_traffic.db"

    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Junction(id="A", name="Test Junction"))
        session.commit()

    def override_db():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_db

    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def arrival(
    event_id="event-1",
    vehicle_id="VH-1",
    direction="NORTH",
    vehicle_type="TRUCK",
    sequence_no=1,
):
    return {
        "event_id": event_id,
        "junction_id": "A",
        "direction": direction,
        "event_type": "VEHICLE_ARRIVED",
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle_type,
        "sequence_no": sequence_no,
        "timestamp": "2026-10-08T10:00:00Z",
    }


def test_vehicle_arrival_updates_queue(client):
    api, _ = client

    response = api.post("/api/sensor-events", json=arrival())

    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"

    status = api.get("/api/junctions/A/status").json()

    assert status["queues"]["NORTH"] == 1


def test_duplicate_event_does_not_increase_queue(client):
    api, _ = client

    payload = arrival()

    first = api.post("/api/sensor-events", json=payload)
    second = api.post("/api/sensor-events", json=payload)

    assert first.json()["status"] == "ACCEPTED"
    assert second.json()["status"] == "DUPLICATE"

    status = api.get("/api/junctions/A/status").json()

    assert status["queues"]["NORTH"] == 1


def test_vehicle_clearance(client):
    api, _ = client

    api.post("/api/sensor-events", json=arrival())

    payload = {
        "event_id": "event-2",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_CLEARED",
        "vehicle_id": "VH-1",
        "sequence_no": 2,
        "timestamp": "2026-10-08T10:01:00Z",
    }

    response = api.post("/api/sensor-events", json=payload)

    assert response.json()["status"] == "ACCEPTED"

    status = api.get("/api/junctions/A/status").json()

    assert status["queues"]["NORTH"] == 0


def test_emergency_arrival_changes_mode(client):
    api, _ = client

    response = api.post(
        "/api/sensor-events",
        json=arrival(
            vehicle_type="EMERGENCY",
            direction="EAST",
        ),
    )

    assert response.json()["emergency_active"] is True

    status = api.get("/api/junctions/A/status").json()

    assert status["mode"] == "EMERGENCY"
    assert status["queues"]["EAST"] == 1


def test_unknown_junction_returns_404(client):
    api, _ = client

    payload = arrival()
    payload["junction_id"] = "UNKNOWN"

    response = api.post("/api/sensor-events", json=payload)

    assert response.status_code == 404


def test_conflicting_duplicate_event_id(client):
    api, _ = client

    api.post("/api/sensor-events", json=arrival())

    modified = arrival(direction="WEST")

    response = api.post("/api/sensor-events", json=modified)

    assert response.status_code == 409


def test_stale_sequence_is_rejected(client):
    api, engine = client

    api.post(
        "/api/sensor-events",
        json=arrival(sequence_no=10),
    )

    payload = {
        "event_id": "event-2",
        "junction_id": "A",
        "direction": "NORTH",
        "event_type": "VEHICLE_CLEARED",
        "vehicle_id": "VH-1",
        "sequence_no": 9,
        "timestamp": "2026-10-08T10:01:00Z",
    }

    response = api.post("/api/sensor-events", json=payload)

    assert response.json()["status"] == "REJECTED"

    with Session(engine) as session:
        stored = session.get(SensorEvent, "event-2")

        assert stored.processing_status == "REJECTED"


def test_invalid_vehicle_type_returns_422(client):
    api, _ = client

    response = api.post(
        "/api/sensor-events",
        json=arrival(vehicle_type="UNKNOWN_TYPE"),
    )

    assert response.status_code == 422
