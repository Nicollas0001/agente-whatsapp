from fastapi import APIRouter, Depends
from sqlalchemy import insert, select
from sqlalchemy.orm import Session
from app.models.cliente import clientes
from app.models.database import SessionLocal
from app.schemas.cliente import ClienteIn, ClienteOut
from typing import List

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/", response_model=List[ClienteOut])
def listar(db: Session = Depends(get_db)):
    result = db.execute(select(clientes)).fetchall()
    return [ClienteOut(**dict(r._mapping)) for r in result]

@router.post("/", response_model=ClienteOut)
def criar(item: ClienteIn, db: Session = Depends(get_db)):
    result = db.execute(insert(clientes).values(**item.dict()))
    db.commit()
    return {**item.dict(), "id": result.lastrowid}
