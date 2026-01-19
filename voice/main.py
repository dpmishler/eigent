import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router as voice_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Eigent Voice Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(voice_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "voice"}


if __name__ == "__main__":
    import uvicorn
    from app.config import settings

    logger.info("Starting voice service on port %d", settings.voice_service_port)
    uvicorn.run(app, host="0.0.0.0", port=settings.voice_service_port)
