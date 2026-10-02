"""API do app (financas.html instalado no tablet) e os arquivos do PWA em /app."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from app.secretaria import acesso, diario, push, rotinas, repositorio as repo

RAIZ = Path(__file__).resolve().parent.parent.parent
ARQUIVOS_PWA = {
    "manifest.webmanifest": "application/manifest+json",
    "sw.js": "application/javascript",
    "icone-192.png": "image/png",
    "icone-512.png": "image/png",
}

api = APIRouter(dependencies=[Depends(acesso.dono)])
pwa = APIRouter()


def _tarefa_json(t) -> dict:
    return {
        "id": t.id, "titulo": t.titulo, "detalhes": t.detalhes, "area": t.area, "status": t.status,
        "prazo": t.prazo.isoformat() if t.prazo else None,
        "agendada_para": t.agendada_para.isoformat() if t.agendada_para else None,
        "hora": t.hora, "prioridade": t.prioridade, "esforco_min": t.esforco_min, "contexto": t.contexto,
        "recorrencia": t.recorrencia, "adiamentos": t.adiamentos,
    }


def _erro(funcao, *args):
    try:
        return funcao(*args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@api.get("/config")
def configuracao(dono: dict = Depends(acesso.dono)):
    return {"vapid": push.chave_publica(), "email": dono.get("email")}


@api.post("/mensagem")
def mensagem(texto: str = Body(..., embed=True)):
    return _erro(rotinas.responder_app, texto)


@api.get("/conversa")
def conversa(antes: Optional[int] = None, limite: int = 60):
    linhas = repo.historico_recente(min(limite, 200), antes_de_id=antes, horas=24 * 90)
    return [{"id": m.id, "papel": m.papel, "texto": m.texto, "momento": m.momento.isoformat()} for m in linhas]


@api.post("/desfazer")
def desfazer():
    with rotinas.vez():
        texto = diario.desfazer_ultima()
        repo.salvar_mensagem("assistant", f"↩️ {texto}")
    return {"resposta": texto}


@api.get("/tarefas")
def tarefas():
    return [_tarefa_json(t) for t in repo.tarefas_abertas()]


@api.post("/tarefas")
def criar_tarefa(dados: dict = Body(...)):
    tarefa_id = _erro(repo.criar_tarefa, dados)
    return _tarefa_json(repo.obter_tarefa(tarefa_id))


@api.patch("/tarefas/{tarefa_id}")
def atualizar_tarefa(tarefa_id: int, dados: dict = Body(...)):
    _erro(repo.atualizar_tarefa, tarefa_id, dados)
    return _tarefa_json(repo.obter_tarefa(tarefa_id))


@api.post("/tarefas/{tarefa_id}/concluir")
def concluir_tarefa(tarefa_id: int):
    return {"resposta": _erro(repo.concluir_tarefa, tarefa_id)}


@api.post("/push")
def inscrever(inscricao: dict = Body(...)):
    _erro(push.salvar_inscricao, inscricao)
    if not repo.estado_get("iniciado"):
        repo.estado_set("iniciado", repo.agora().isoformat())
    return {"ok": True}


@api.post("/push/teste")
def testar_push():
    return {"entregues": push.enviar("Secretária", "Notificações ligadas. É assim que eu vou te chamar.")}


@pwa.get("", include_in_schema=False)
def app_sem_barra():
    return RedirectResponse("/app/")


@pwa.get("/", include_in_schema=False)
def app_inicio():
    return FileResponse(RAIZ / "financas.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@pwa.get("/{arquivo}", include_in_schema=False)
def app_arquivo(arquivo: str):
    if arquivo not in ARQUIVOS_PWA:
        raise HTTPException(status_code=404)
    return FileResponse(RAIZ / "pwa" / arquivo, media_type=ARQUIVOS_PWA[arquivo],
                        headers={"Cache-Control": "no-cache"})
