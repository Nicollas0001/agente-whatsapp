from fastapi import APIRouter, Depends
from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from app.models.produto import produtos
from app.models.database import SessionLocal
from app.schemas.produto import ProdutoIn, ProdutoOut
from typing import List

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[ProdutoOut])
def listar(db: Session = Depends(get_db)):
    result = db.execute(select(produtos)).fetchall()
    return [ProdutoOut(**dict(r._mapping)) for r in result]

@router.post("/", response_model=ProdutoOut)
def criar(item: ProdutoIn, db: Session = Depends(get_db)):
    result = db.execute(insert(produtos).values(**item.dict()))
    db.commit()
    return {**item.dict(), "id": result.lastrowid}
