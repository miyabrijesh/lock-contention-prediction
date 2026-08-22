from fastapi import FastAPI

from backend.app.api.routes.bookings import router as bookings_router
from backend.app.api.routes.health import router as health_router
from backend.app.api.routes.shows import router as shows_router


app = FastAPI(
    title="Lock Contention Prediction API",
    description=(
        "API for a controlled PostgreSQL reservation workload, "
        "contention monitoring, and future-risk prediction."
    ),
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(shows_router)
app.include_router(bookings_router)


@app.get(
    "/",
    include_in_schema=False,
)
def application_root() -> dict[str, str]:
    """Provide a small root response with a documentation link."""

    return {
        "service": "lock-contention-api",
        "documentation": "/docs",
    }