from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.config import AUTO_SEED, FRONTEND_DIR
from app.db import init_db
from app.errors import BusinessError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if AUTO_SEED:
        from app.seed import seed

        seed()
    yield


app = FastAPI(title="差旅合规与单据核对助手", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(BusinessError)
def handle_business(_request, exc: BusinessError):
    return JSONResponse(status_code=exc.http, content={"error_code": exc.code, "message": exc.message})


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
