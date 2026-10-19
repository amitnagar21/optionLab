from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from naked_put_api import router as naked_put_router

app = FastAPI(title="Naked Put Backtest API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(naked_put_router)


@app.get("/health")
def health():
    return {"ok": True}

# Run locally:  uvicorn main:app --reload
# Docs/test UI: http://127.0.0.1:8000/docs
