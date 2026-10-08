
from sqlalchemy import inspect, text

from .session import engine


def migrate_manual_control() -> None:
    """Add persisted manual phase without deleting existing data."""

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("junctions")
    }

    if "manual_phase" in columns:
        print("Manual control migration already applied.")
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE junctions "
                "ADD COLUMN manual_phase VARCHAR(30)"
            )
        )

    print("Manual control migration completed.")


if __name__ == "__main__":
    migrate_manual_control()
