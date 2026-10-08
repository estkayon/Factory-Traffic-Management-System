
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.routes.controller_events import (
    router as controller_router,
)
from backend.app.api.routes.junctions import (
    router as junction_router,
)
from backend.app.api.routes.sensor_events import (
    router as sensor_event_router,
)
from backend.app.infrastructure.controllers.recovery_service import (
    ControllerRecoveryService,
)
from backend.app.infrastructure.database.init_db import (
    initialize_database,
)
from backend.app.infrastructure.database.session import SessionLocal

from backend.app.api.routes.traffic_control import (
    router as traffic_control_router,
)

from backend.app.api.routes.manual_control import (
    router as manual_control_router,
)

from backend.app.api.routes.device_status import (
    router as device_status_router,
)
from backend.app.api.routes.junction_details import (
    router as junction_details_router,
)

from pathlib import Path
from fastapi.staticfiles import StaticFiles

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize storage and safely recover controller state."""

    initialize_database()

    with SessionLocal() as session:
        ControllerRecoveryService(session).recover_after_restart()

    yield


app = FastAPI(
    title="Factory Traffic Management System",
    description="Event-driven factory traffic management backend.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(junction_router)
app.include_router(sensor_event_router)
app.include_router(controller_router)
app.include_router(traffic_control_router)
app.include_router(manual_control_router)
app.include_router(device_status_router)
app.include_router(junction_details_router)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app.mount(
    "/dashboard",
    StaticFiles(directory=str(FRONTEND_DIR), html=True),
    name="dashboard",
)



@app.get("/api/health", tags=["Health"])
def health_check() -> dict[str, str]:
    """Return the current application health status."""
    return {"status": "healthy"}
