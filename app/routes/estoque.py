from fastapi import APIRouter, Depends
from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from app.models.estoque import estoque
from app.models.database import SessionLocal
from app.schemas.estoque import EstoqueIn, EstoqueOut
from typing import List

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[EstoqueOut])
def listar(db: Session = Depends(get_db)):
    result = db.execute(select(estoque)).fetchall()
    return [EstoqueOut(**dict(r._mapping)) for r in result]

@router.post("/", response_model=EstoqueOut)
def criar(item: EstoqueIn, db: Session = Depends(get_db)):
    result = db.execute(insert(estoque).values(**item.dict()))
    db.commit()
    return {**item.dict(), "id": result.lastrowid}
