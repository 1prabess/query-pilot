"""Every route the API serves, in one place."""

from fastapi import APIRouter

from app.api.routes import (
    admin,
    auth,
    er,
    health,
    project,
    query,
    schema,
    subscription,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(project.router)
api_router.include_router(schema.router)
api_router.include_router(er.router)
api_router.include_router(query.router)
api_router.include_router(subscription.router)
api_router.include_router(subscription.plans_router)
api_router.include_router(admin.router)
