from fastapi import FastAPI
from app.routes import zap
from app.models import database

app = FastAPI()

database.create_tables()

app.include_router(zap.router, prefix="/zap", tags=["Zap"])

@app.get("/")
def home():
    return {"msg": "API online Render"}
