"""Diário das ações da IA, para poder desfazer.

Durante uma rodada da IA (`with registrando("...")`), o repositório e o módulo
de finanças anotam aqui como as coisas estavam antes de cada alteração. No fim
da rodada, o diário vira um documento em sec_desfazer. Desfazer devolve só o
que a IA mexeu: itens das finanças são restaurados um a um, pelo id, para não
apagar o que foi feito depois no app.
"""
import contextvars
import json
import time
from contextlib import contextmanager
from typing import Optional

_atual = contextvars.ContextVar("diario", default=None)


class Diario:
    def __init__(self, descricao: str):
        self.descricao = descricao
        self.docs = {}       # (coleção, id) -> dados antes (None = não existia)
        self.financas = {}   # campo -> diferença em relação ao que havia antes

    def vazio(self) -> bool:
        return not self.docs and not self.financas

    def anotar_doc(self, colecao: str, doc_id: str, antes: Optional[dict]) -> None:
        self.docs.setdefault((colecao, str(doc_id)), antes)

    def anotar_financas(self, antes: dict, mudancas: dict) -> None:
        for campo, depois in mudancas.items():
            if campo in ("rev", "updatedAt", "alteradoPor"):
                continue
            anterior = antes.get(campo)
            diferenca = diferenca_campo(anterior, depois)
            if campo not in self.financas:
                self.financas[campo] = diferenca
            else:
                self.financas[campo] = juntar_diferencas(self.financas[campo], diferenca)


def chave(item) -> str:
    if isinstance(item, dict) and item.get("id") is not None:
        return f"id:{item['id']}"
    return "v:" + json.dumps(item, sort_keys=True, ensure_ascii=False)


def diferenca_campo(antes, depois) -> dict:
    if isinstance(antes, list) or isinstance(depois, list):
        a = {chave(i): i for i in antes or []}
        d = {chave(i): i for i in depois or []}
        return {
            "tipo": "lista",
            "adicionados": [k for k in d if k not in a],
            "removidos": [a[k] for k in a if k not in d],
            "alterados": [a[k] for k in a if k in d and json.dumps(a[k], sort_keys=True) != json.dumps(d[k], sort_keys=True)],
        }
    return {"tipo": "valor", "antes": antes}


def juntar_diferencas(primeira: dict, segunda: dict) -> dict:
    """Duas alterações no mesmo campo dentro de uma rodada: o desfazer volta ao estado do começo."""
    if primeira["tipo"] == "valor":
        return primeira
    criados_na_rodada = set(primeira["adicionados"])
    removidos = list(primeira["removidos"])
    alterados = {chave(i): i for i in primeira["alterados"]}
    adicionados = list(primeira["adicionados"])
    for k in segunda["adicionados"]:
        if k not in adicionados:
            adicionados.append(k)
    for item in segunda["removidos"]:
        k = chave(item)
        if k in criados_na_rodada:
            continue                         # criado e apagado na mesma rodada: nada a fazer
        removidos.append(alterados.pop(k, item))
    for item in segunda["alterados"]:
        k = chave(item)
        if k not in criados_na_rodada and k not in alterados:
            alterados[k] = item
    return {"tipo": "lista", "adicionados": adicionados, "removidos": removidos, "alterados": list(alterados.values())}


def ativo() -> Optional[Diario]:
    return _atual.get()


@contextmanager
def registrando(descricao: str):
    diario = Diario(descricao)
    token = _atual.set(diario)
    try:
        yield diario
    finally:
        _atual.reset(token)
        if not diario.vazio():
            salvar(diario)


@contextmanager
def pausado():
    token = _atual.set(None)
    try:
        yield
    finally:
        _atual.reset(token)


def _colecao():
    from app.secretaria import banco
    return banco.cliente().collection("sec_desfazer")


def salvar(diario: Diario) -> int:
    from app.secretaria import repositorio as repo
    seq = time.time_ns()
    diario.seq = seq
    conteudo = {
        "docs": [{"colecao": c, "id": i, "antes": a} for (c, i), a in diario.docs.items()],
        "financas": diario.financas,
    }
    _colecao().document(str(seq)).set({
        "seq": seq, "momento": repo.agora().isoformat(), "descricao": diario.descricao[:300],
        "conteudo": json.dumps(conteudo, ensure_ascii=False, default=str), "desfeito": False,
    })
    return seq


def _desfazer_financas(diferencas: dict) -> None:
    from app.secretaria import financas

    def aplicar(d):
        mudancas = {}
        for campo, dif in diferencas.items():
            if dif["tipo"] == "valor":
                mudancas[campo] = dif["antes"]
                continue
            adicionados = set(dif["adicionados"])
            antigos = {chave(i): i for i in dif["alterados"]}
            atual = [antigos.get(chave(i), i) for i in d.get(campo) or [] if chave(i) not in adicionados]
            presentes = {chave(i) for i in atual}
            atual += [i for i in dif["removidos"] if chave(i) not in presentes]
            mudancas[campo] = atual
        return mudancas, None

    financas.alterar(aplicar)


def desfazer_ultima() -> str:
    """Desfaz a ação mais recente da IA que ainda não foi desfeita."""
    from app.secretaria import banco
    from google.cloud import firestore

    recentes = (_colecao().order_by("seq", direction=firestore.Query.DESCENDING).limit(20).stream())
    alvo = next((s for s in recentes if not s.to_dict().get("desfeito")), None)
    if alvo is None:
        return "Não tem nenhuma ação minha para desfazer."
    registro = alvo.to_dict()
    conteudo = json.loads(registro["conteudo"])
    with pausado():
        lote = banco.cliente().batch()
        for doc in conteudo["docs"]:
            ref = banco.cliente().collection(doc["colecao"]).document(doc["id"])
            if doc["antes"] is None:
                lote.delete(ref)
            else:
                lote.set(ref, doc["antes"])
        lote.commit()
        if conteudo["financas"]:
            _desfazer_financas(conteudo["financas"])
    alvo.reference.update({"desfeito": True})
    return f"Desfeito: {registro['descricao']}"
