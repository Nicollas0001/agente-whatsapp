from pydantic import BaseModel

class ProdutoIn(BaseModel):
    nome: str
    preco: float
    estoque: int

class ProdutoOut(ProdutoIn):
    id: int
