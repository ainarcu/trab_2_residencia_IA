from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from src.api.routes_risk import router as risk_router
except ImportError:
    from routes_risk import router as risk_router

app = FastAPI(
    title="POA Flood ML API",
    description="Previsao probabilistica de enchentes e impacto focalizado em Porto Alegre.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(risk_router)


@app.get("/health", tags=["system"])
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "region": "Porto Alegre - RS"}
