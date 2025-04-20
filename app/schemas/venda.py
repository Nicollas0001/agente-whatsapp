from pydantic import BaseModel

class VendaIn(BaseModel):
    cliente_id: int
    produto_id: int
    quantidade: int

class VendaOut(VendaIn):
    id: int
    total: float
