from fastapi import FastAPI
from app.auth.routes import router as auth_router
from app.targets.routes import router as targets_router

app = FastAPI()

app.include_router(auth_router, prefix='/auth')
app.include_router(targets_router, prefix = '/targets')