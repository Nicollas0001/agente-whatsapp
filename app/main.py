from fastapi import FastAPI
from app.routes import zap

app = FastAPI()

app.include_router(zap.router, prefix="/zap")

@app.get("/")
def home():
    return {"msg": "API online"}
