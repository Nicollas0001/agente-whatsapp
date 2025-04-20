from fastapi import APIRouter, Depends
from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from app.models.venda import vendas
from app.models.produto import produtos
from app.models.database import SessionLocal
from app.schemas.venda import VendaIn, VendaOut
from typing import List

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[VendaOut])
def listar(db: Session = Depends(get_db)):
    result = db.execute(select(vendas)).fetchall()
    return [VendaOut(**dict(r._mapping)) for r in result]

@router.post("/", response_model=VendaOut)
def criar(venda: VendaIn, db: Session = Depends(get_db)):
    produto = db.execute(select(produtos).where(produtos.c.id == venda.produto_id)).first()
    total = produto._mapping["preco"] * venda.quantidade
    result = db.execute(insert(vendas).values(**venda.dict(), total=total))
    db.commit()
    return {**venda.dict(), "id": result.lastrowid, "total": total}
