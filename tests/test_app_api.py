import base64
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.secretaria import acesso, config, ia, push, rotinas, repositorio as repo

DONO = "dono@teste.com"


@pytest.fixture
def cliente(monkeypatch, relogio):
    monkeypatch.setattr(config, "DONO_EMAIL", DONO)
    tokens = {"bom": {"email": DONO, "email_verified": True},
              "intruso": {"email": "outro@teste.com", "email_verified": True},
              "nao_verificado": {"email": DONO, "email_verified": False}}

    def verificar(token):
        if token not in tokens:
            raise ValueError("token inválido")
        return tokens[token]

    monkeypatch.setattr(acesso, "verificar_token", verificar)
    from app.main import app
    c = TestClient(app)
    c.headers.update({"Authorization": "Bearer bom"})
    return c


def test_so_o_dono_entra(cliente):
    assert cliente.get("/secretaria/app/tarefas", headers={"Authorization": ""}).status_code == 401
    assert cliente.get("/secretaria/app/tarefas", headers={"Authorization": "Bearer lixo"}).status_code == 401
    assert cliente.get("/secretaria/app/tarefas", headers={"Authorization": "Bearer intruso"}).status_code == 403
    assert cliente.get("/secretaria/app/tarefas", headers={"Authorization": "Bearer nao_verificado"}).status_code == 403
    assert cliente.get("/secretaria/app/tarefas").status_code == 200


def test_conversa_pelo_app_com_desfazer(cliente, monkeypatch):
    def responder(pedido, planejamento=False):
        repo.criar_tarefa({"titulo": "Pagar IPVA", "area": "pessoal"})
        return "Anotei o <b>IPVA</b>."

    monkeypatch.setattr(ia, "responder", responder)
    r = cliente.post("/secretaria/app/mensagem", json={"texto": "preciso pagar o IPVA"}).json()
    assert r["resposta"] == "Anotei o <b>IPVA</b>." and r["desfazivel"] is True
    assert [t["titulo"] for t in cliente.get("/secretaria/app/tarefas").json()] == ["Pagar IPVA"]
    assert repo.estado_get("iniciado")

    historico = cliente.get("/secretaria/app/conversa").json()
    assert [(m["papel"], m["texto"]) for m in historico] == [("user", "preciso pagar o IPVA"),
                                                           ("assistant", "Anotei o <b>IPVA</b>.")]

    assert cliente.post("/secretaria/app/desfazer").json()["resposta"] == "Desfeito: preciso pagar o IPVA"
    assert cliente.get("/secretaria/app/tarefas").json() == []
    assert cliente.get("/secretaria/app/conversa").json()[-1]["texto"].startswith("↩️ Desfeito")


def test_falha_no_meio_responde_e_deixa_desfazer(cliente, monkeypatch):
    def quebra_no_meio(pedido, planejamento=False):
        repo.criar_tarefa({"titulo": "Meio caminho", "area": "pessoal"})
        raise RuntimeError("sem crédito")

    monkeypatch.setattr(ia, "responder", quebra_no_meio)
    r = cliente.post("/secretaria/app/mensagem", json={"texto": "faz várias coisas"})
    assert r.status_code == 200
    corpo = r.json()
    assert "problema técnico" in corpo["resposta"] and "desfeito" in corpo["resposta"] and corpo["desfazivel"]
    cliente.post("/secretaria/app/desfazer")
    assert cliente.get("/secretaria/app/tarefas").json() == []


def _erro_api(classe, status, mensagem):
    import anthropic
    import httpx2
    resposta = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    corpo = {"type": "error", "error": {"type": "invalid_request_error", "message": mensagem}}
    return getattr(anthropic, classe)(f"Error code: {status}", response=resposta, body=corpo)


@pytest.mark.parametrize("classe,status,mensagem,esperado", [
    ("BadRequestError", 400, "Your credit balance is too low to access the Anthropic API.", "acabou o crédito"),
    ("BadRequestError", 400, "tools.3.custom.input_schema: <bad>", "BadRequestError: tools.3.custom.input_schema: &lt;bad&gt;"),
    ("AuthenticationError", 401, "invalid x-api-key", "recusou a chave"),
])
def test_falha_da_api_diz_o_motivo(cliente, monkeypatch, classe, status, mensagem, esperado):
    def falha(pedido, planejamento=False):
        raise _erro_api(classe, status, mensagem)

    monkeypatch.setattr(ia, "responder", falha)
    corpo = cliente.post("/secretaria/app/mensagem", json={"texto": "oi"}).json()
    assert esperado in corpo["resposta"]

def test_comandos_sem_ia_pelo_app(cliente):
    r = cliente.post("/secretaria/app/mensagem", json={"texto": "/tarefas"}).json()
    assert "Nada em aberto" in r["resposta"] and r["desfazivel"] is False
    assert cliente.post("/secretaria/app/mensagem", json={"texto": "  "}).status_code == 400


def test_tarefas_na_mao_pelo_app(cliente):
    criada = cliente.post("/secretaria/app/tarefas", json={"titulo": "Ligar pro contador", "area": "profissional",
                                                          "agendada_para": "2026-10-01"}).json()
    assert criada["id"] == 1 and criada["agendada_para"] == "2026-10-01"
    editada = cliente.patch("/secretaria/app/tarefas/1", json={"agendada_para": "2026-10-03", "prioridade": 1}).json()
    assert (editada["agendada_para"], editada["prioridade"], editada["adiamentos"]) == ("2026-10-03", 1, 1)
    assert cliente.patch("/secretaria/app/tarefas/1", json={"status": "voando"}).status_code == 400
    assert "concluída" in cliente.post("/secretaria/app/tarefas/1/concluir").json()["resposta"]
    assert cliente.get("/secretaria/app/tarefas").json() == []
    assert cliente.post("/secretaria/app/tarefas", json={"area": "pessoal"}).status_code == 400


def _inscricao_de_navegador():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    privada = ec.generate_private_key(ec.SECP256R1())
    publica = privada.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    segredo = os.urandom(16)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return privada, segredo, {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                               "keys": {"p256dh": b64(publica), "auth": b64(segredo)}}


def test_notificacao_chega_criptografada_e_legivel(cliente, monkeypatch):
    import http_ece
    import pywebpush
    privada, segredo, inscricao = _inscricao_de_navegador()
    assert cliente.post("/secretaria/app/push", json=inscricao).json() == {"ok": True}
    assert len(cliente.get("/secretaria/app/config").json()["vapid"]) > 80

    enviados = []

    def post(url, data=None, headers=None, timeout=None, **_):
        enviados.append((url, data, headers))
        return SimpleNamespace(status_code=201, text="", reason="Created", headers={})

    monkeypatch.setattr(pywebpush.requests, "post", post)
    assert push.enviar("Secretária", "Bom dia! Hoje vence o aluguel.") == 1
    url, corpo, cabecalhos = enviados[0]
    assert url == inscricao["endpoint"] and cabecalhos["Authorization"].startswith("vapid t=")
    claro = http_ece.decrypt(corpo, private_key=privada, auth_secret=segredo, version="aes128gcm")
    assert json.loads(claro)["corpo"] == "Bom dia! Hoje vence o aluguel."

    # aparelho que cancelou a inscrição sai da lista
    def post_expirado(url, **_):
        return SimpleNamespace(status_code=410, text="gone", reason="Gone", headers={})

    monkeypatch.setattr(pywebpush.requests, "post", post_expirado)
    assert push.enviar("Secretária", "oi") == 0
    assert push.inscricoes() == []


def test_rotina_avisa_no_tablet_sem_telegram(cliente, monkeypatch, relogio):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    notificacoes = []
    monkeypatch.setattr(push, "enviar", lambda titulo, corpo, **k: notificacoes.append(corpo) or 1)
    monkeypatch.setattr(push, "inscricoes", lambda: [{"endpoint": "https://x"}])
    monkeypatch.setattr(ia, "responder", lambda pedido, planejamento=False: "<b>Bom dia!</b> 3 prioridades hoje.")
    repo.estado_set("iniciado", "sim")
    repo.criar_lembrete("2026-10-01 05:50", "Tomar o remédio & água")
    relogio.ajustar(datetime(2026, 10, 1, 6, 5))
    assert rotinas.tick() == {"lembretes": 1, "plano": True, "fechamento": False}
    assert notificacoes == ["⏰ Tomar o remédio & água", "Bom dia! 3 prioridades hoje."]
    textos = [m["texto"] for m in cliente.get("/secretaria/app/conversa").json()]
    assert textos[-1] == "<b>Bom dia!</b> 3 prioridades hoje."


def test_arquivos_do_app(cliente):
    sem_login = TestClient(cliente.app)
    pagina = sem_login.get("/app/")
    assert pagina.status_code == 200 and "manifest.webmanifest" in pagina.text
    assert sem_login.get("/app", follow_redirects=False).headers["location"] == "/app/"
    assert sem_login.get("/app/manifest.webmanifest").json()["display"] == "standalone"
    assert "showNotification" in sem_login.get("/app/sw.js").text
    assert sem_login.get("/app/icone-512.png").headers["content-type"] == "image/png"
    assert sem_login.get("/app/dados_iniciais.json").status_code == 404   # nada além do app sai daqui
    assert sem_login.get("/app/..%2Fagente.db").status_code == 404
