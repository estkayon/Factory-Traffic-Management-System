
from sqlalchemy import select

from .base import Base
from .models import Junction, SignalState
from .session import SessionLocal, engine


DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")


def initialize_database() -> None:
    """Create database tables and seed the initial junction."""

    Base.metadata.create_all(bind=engine)

    with SessionLocal.begin() as session:
        junction = session.get(Junction, "A")

        if junction is None:
            session.add(
                Junction(
                    id="A",
                    name="Factory Junction A",
                    mode="AUTOMATIC",
                    active_phase="ALL_RED",
                    controller_status="UNKNOWN",
                )
            )
            session.flush()

        existing_directions = set(
            session.scalars(
                select(SignalState.direction).where(
                    SignalState.junction_id == "A"
                )
            ).all()
        )

        for direction in DIRECTIONS:
            if direction not in existing_directions:
                session.add(
                    SignalState(
                        junction_id="A",
                        direction=direction,
                        desired_state="RED",
                        actual_state="UNKNOWN",
                    )
                )


if __name__ == "__main__":
    initialize_database()
    print("Database initialized successfully.")
