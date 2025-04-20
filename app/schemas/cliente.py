from pydantic import BaseModel

class ClienteIn(BaseModel):
    nome: str
    cpf_cnpj: str
    telefone: str

class ClienteOut(ClienteIn):
    id: int
