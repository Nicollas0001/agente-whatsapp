from pydantic import BaseModel

class EstoqueIn(BaseModel):
    sku: str
    tipo: str
    quantidade: int

class EstoqueOut(EstoqueIn):
    id: int
