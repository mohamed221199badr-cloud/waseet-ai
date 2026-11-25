from fastapi import FastAPI

from app.api.routes import health, products
from app.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(health.router)
app.include_router(products.router, prefix="/api/v1")
