from fastapi import FastAPI
from app.routes.case import router as case_router

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

app.include_router(case_router)