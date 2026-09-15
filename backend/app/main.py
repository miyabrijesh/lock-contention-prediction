from fastapi import FastAPI
from fastapi.middleware.cors import (
    CORSMiddleware,
)

from backend.app.api.routes.bookings import (
    router as bookings_router,
)
from backend.app.api.routes.demo import (
    router as demo_router,
)
from backend.app.api.routes.experiments import (
    router as experiments_router,
)
from backend.app.api.routes.health import (
    router as health_router,
)
from backend.app.api.routes.models import (
    router as models_router,
)
from backend.app.api.routes.predictions import (
    router as predictions_router,
)
from backend.app.api.routes.shows import (
    router as shows_router,
)


app = FastAPI(
    title="Lock Contention Prediction API",
    description=(
        "API for a controlled PostgreSQL reservation workload, "
        "contention monitoring, and future-risk prediction."
    ),
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=[
        "GET",
        "POST",
        "OPTIONS",
    ],
    allow_headers=[
        "Accept",
        "Content-Type",
        "Idempotency-Key",
    ],
)


app.include_router(health_router)
app.include_router(shows_router)
app.include_router(bookings_router)
app.include_router(experiments_router)
app.include_router(models_router)
app.include_router(predictions_router)
app.include_router(demo_router)


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