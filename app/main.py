from fastapi import FastAPI

from app.api.routes import (
    auth,
    customers,
    health,
    intake,
    inventory,
    orders,
    products,
    telegram,
)
from app.config import settings

app = FastAPI(title=settings.app_name, version="0.4.0")
app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(products.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(inventory.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")
app.include_router(intake.router, prefix="/api/v1")
app.include_router(telegram.router, prefix="/api/v1")
