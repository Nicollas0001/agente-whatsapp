"""App financas.html de verdade no navegador + secretária gravando no mesmo Firestore.

Precisa dos emuladores de Firestore e Auth com as regras do repositório
(e-mail trocado para dono@teste.com), do Playwright e dos arquivos compat do
Firebase 9.23.0 numa pasta local (o teste os serve no lugar do gstatic.com):

    sed 's/SEU_EMAIL@gmail.com/dono@teste.com/' firestore.rules > /tmp/emu/firestore.rules
    firebase emulators:start --only firestore,auth   # com firebase.json apontando as regras
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099 \\
    FIREBASE_SDK_DIR=/caminho/dos/compat python -m pytest tests/test_app_e2e.py
"""
import functools
import http.server
import json
import os
import threading
import time
from pathlib import Path

import pytest
import requests

pytest.importorskip("playwright")
if not (os.getenv("FIREBASE_AUTH_EMULATOR_HOST") and os.getenv("FIREBASE_SDK_DIR")):
    pytest.skip("precisa de FIREBASE_AUTH_EMULATOR_HOST e FIREBASE_SDK_DIR", allow_module_level=True)

from google.cloud import firestore  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app.secretaria import banco, financas as fin, repositorio as repo  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PROJETO = "financas-pessoal-1ca66"  # o mesmo do app
FIRESTORE = os.environ["FIRESTORE_EMULATOR_HOST"]
AUTH = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]


@pytest.fixture
def servidor():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RAIZ))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/financas.html?emulador"
    httpd.shutdown()


@pytest.fixture
def secretaria(monkeypatch, relogio):
    """A secretária apontando para o mesmo projeto do app, com os dados reais de exemplo."""
    requests.delete(f"http://{FIRESTORE}/emulator/v1/projects/{PROJETO}/databases/(default)/documents").raise_for_status()
    requests.delete(f"http://{AUTH}/emulator/v1/projects/{PROJETO}/accounts").raise_for_status()
    cliente = firestore.Client(project=PROJETO)
    monkeypatch.setattr(banco, "_cliente", cliente)
    dados = json.loads((RAIZ / "dados_iniciais.json").read_text())
    dados.pop("exportadoEm", None)
    dados.pop("config", None)
    dados.update({"rev": 1, "versao": "v20260519-7"})
    cliente.collection("financas").document("dados").set(dados)
    return cliente


@pytest.fixture
def pagina(servidor):
    sdk = Path(os.environ["FIREBASE_SDK_DIR"])
    with sync_playwright() as p:
        navegador = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        pagina = navegador.new_page()
        pagina.route("https://www.gstatic.com/firebasejs/**",
                     lambda rota: rota.fulfill(path=str(sdk / rota.request.url.rsplit("/", 1)[1]),
                                               content_type="application/javascript"))
        pagina.goto(servidor)
        yield pagina
        navegador.close()


def _entrar(pagina, email):
    pagina.evaluate("""email => firebase.auth().signInWithCredential(
        firebase.auth.GoogleAuthProvider.credential(JSON.stringify({sub: email, email, email_verified: true})))""", email)


def _esperar(condicao, segundos=15, descricao=""):
    limite = time.time() + segundos
    while time.time() < limite:
        if condicao():
            return
        time.sleep(0.3)
    raise AssertionError(f"não aconteceu em {segundos}s: {descricao}")


def _descricoes_app(pagina):
    return {t["descricao"] for t in pagina.evaluate("Storage.transacoes()")}


def _descricoes_firestore(cliente):
    return {t["descricao"] for t in cliente.collection("financas").document("dados").get().to_dict()["transacoes"]}


def test_app_e_secretaria_no_mesmo_dado(secretaria, pagina):
    # sem login: não semeia dados de exemplo e pede para entrar
    _esperar(lambda: pagina.text_content("#sync-dot") == "🔑", descricao="pedir login")
    assert pagina.evaluate("localStorage.getItem('ff_transacoes')") is None

    # outra conta Google: as regras barram
    _entrar(pagina, "intruso@teste.com")
    _esperar(lambda: pagina.text_content("#sync-dot") == "🔴", descricao="barrar conta errada")
    assert pagina.evaluate("localStorage.getItem('ff_transacoes')") is None
    pagina.evaluate("firebase.auth().signOut()")

    # a conta certa baixa os dados
    _entrar(pagina, "dono@teste.com")
    _esperar(lambda: "Salário Marista" in _descricoes_app(pagina), descricao="baixar dados")

    # a secretária lança e o app recebe na hora, com aviso
    fin.lancar([{"tipo": "despesa", "valor": 52, "descricao": "Pizza", "categoria": "Alimentação", "cartao": "Nubank"}])
    _esperar(lambda: "Pizza" in _descricoes_app(pagina), descricao="receber lançamento da secretária")
    assert "secretária" in pagina.text_content("#toast")

    # o app lança e a secretária enxerga
    pagina.evaluate("""() => { const a = Storage.transacoes();
        a.push({id: 'app-1', data: '2026-10-01', descricao: 'Padaria', valor: 12, tipo: 'despesa',
                categoria: null, contaId: null, cartaoId: null, contaDestinoId: null, parcelas: 1, parcelaAtual: 1, parcelaGrupoId: null});
        Storage.salvarTransacoes(a); }""")
    _esperar(lambda: "Padaria" in _descricoes_firestore(secretaria), descricao="app gravar no Firestore")

    # os dois ao mesmo tempo: nada se perde
    pagina.evaluate("""() => { const a = Storage.transacoes();
        a.push({id: 'app-2', data: '2026-10-01', descricao: 'Café', valor: 7, tipo: 'despesa',
                categoria: null, contaId: null, cartaoId: null, contaDestinoId: null, parcelas: 1, parcelaAtual: 1, parcelaGrupoId: null});
        Storage.salvarTransacoes(a); }""")
    fin.lancar([{"tipo": "despesa", "valor": 23, "descricao": "Uber", "categoria": "Transporte", "conta": "Inter"}])
    esperado = {"Pizza", "Padaria", "Café", "Uber"}
    _esperar(lambda: esperado <= _descricoes_firestore(secretaria), descricao="juntar gravações simultâneas")
    _esperar(lambda: esperado <= _descricoes_app(pagina), descricao="app ficar com tudo")

    # apagar no app apaga para a secretária também, sem levar o resto junto
    pagina.evaluate("Storage.salvarTransacoes(Storage.transacoes().filter(t => t.descricao !== 'Pizza'))")
    _esperar(lambda: "Pizza" not in _descricoes_firestore(secretaria), descricao="apagar")
    assert {"Padaria", "Café", "Uber", "Salário Marista"} <= _descricoes_firestore(secretaria)

    # nem o dono acessa direto as coleções da secretária pelo navegador
    erro = pagina.evaluate("""() => firebase.firestore().collection('sec_tarefas').get()
        .then(() => 'leu').catch(e => e.code)""")
    assert erro == "permission-denied"


def test_acesso_barrado_nao_suja_o_firebase_com_a_semente(secretaria, pagina):
    """Tablet abre o app logado mas as regras ainda barram (ex.: regras de teste vencidas)."""
    semente = pagina.evaluate("SEMENTE")
    apagada = next(t for t in semente["transacoes"] if t["descricao"] == "Jiu Jitsu (extra julho)")
    transacoes = [t for t in semente["transacoes"] if t["id"] != apagada["id"]]   # apagada antes, em outro aparelho
    transacoes.append({**transacoes[0], "id": "nova-la", "descricao": "Salário Marista"})
    secretaria.collection("financas").document("dados").update({"transacoes": transacoes})

    _entrar(pagina, "intruso@teste.com")
    _esperar(lambda: pagina.text_content("#sync-dot") == "🔴", descricao="barrar")
    pagina.reload()
    _esperar(lambda: pagina.text_content("#sync-dot") == "🔴", descricao="barrar de novo ao reabrir")
    time.sleep(1)
    assert pagina.evaluate("localStorage.getItem('ff_transacoes')") is None   # não semeia com erro de acesso

    # aparelho que já tinha só a semente (versão antiga do app semeava com erro); o acesso é liberado
    pagina.evaluate("seed()")
    pagina.evaluate("firebase.auth().signOut()")
    _entrar(pagina, "dono@teste.com")
    _esperar(lambda: pagina.text_content("#sync-dot") == "🟢", descricao="sincronizar")
    _esperar(lambda: "Salário Marista" in _descricoes_app(pagina), descricao="baixar dados")
    time.sleep(2)
    ids_firestore = {t["id"] for t in secretaria.collection("financas").document("dados").get().to_dict()["transacoes"]}
    assert ids_firestore == {t["id"] for t in transacoes}                       # nada ressuscitou nem sumiu
    assert apagada["id"] not in {t["id"] for t in pagina.evaluate("Storage.transacoes()")}


# ---------- o app instalado, servido pelo próprio servidor em /app/ ----------

@pytest.fixture
def servidor_api(monkeypatch, secretaria):
    import uvicorn
    from app.secretaria import config, ia, financas as fin_mod

    monkeypatch.setenv("FIREBASE_PROJECT_ID", PROJETO)
    monkeypatch.setattr(config, "DONO_EMAIL", "dono@teste.com")

    def ia_falsa(pedido, planejamento=False):
        if "gastei 45" in pedido:
            fin_mod.lancar([{"tipo": "despesa", "valor": 45, "descricao": "iFood", "categoria": "Alimentação",
                             "cartao": "Nubank"}])
            return 'Lancei <b>R$ 45,00</b> no Nubank. <img src=x onerror="window.__xss=1"><script>window.__xss=2</script>'
        return "Oi! Em que posso ajudar?"

    monkeypatch.setattr(ia, "responder", ia_falsa)

    def testar_chave(valor):
        if valor != "sk-ant-api03-boa":
            import anthropic
            import httpx2
            resposta = httpx2.Response(401, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
            raise anthropic.AuthenticationError("401", response=resposta, body=None)

    monkeypatch.setattr(ia, "testar_chave", testar_chave)
    from app.main import app
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    _esperar(lambda: servidor.started, descricao="subir o servidor")
    porta = servidor.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{porta}"
    servidor.should_exit = True
    thread.join(5)


def test_app_instalado_conversa_desfaz_e_tarefas(servidor_api, secretaria):
    sdk = Path(os.environ["FIREBASE_SDK_DIR"])
    with sync_playwright() as p:
        navegador = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        pagina = navegador.new_page()
        pagina.route("https://www.gstatic.com/firebasejs/**",
                     lambda rota: rota.fulfill(path=str(sdk / rota.request.url.rsplit("/", 1)[1]),
                                               content_type="application/javascript"))
        downloads = []
        pagina.on("download", lambda d: downloads.append(d.url))   # nada pode virar "baixar arquivo"
        pagina.goto(f"{servidor_api}/app/?emulador#secretaria")

        # é um PWA instalável: manifesto e service worker no ar
        assert pagina.evaluate("fetch('manifest.webmanifest').then(r => r.json()).then(m => m.display)") == "standalone"
        assert pagina.evaluate("navigator.serviceWorker.ready.then(r => r.active ? 'ativo' : 'nao')") == "ativo"

        # outra conta: a API recusa
        _entrar(pagina, "intruso@teste.com")
        _esperar(lambda: pagina.locator("#sec-txt").count() == 1, descricao="tela da secretária")
        pagina.fill("#sec-txt", "oi")
        pagina.press("#sec-txt", "Enter")
        _esperar(lambda: "não tem acesso" in pagina.text_content("#sec-hist"), descricao="recusar outra conta")
        pagina.evaluate("firebase.auth().signOut()")

        # o dono conversa; a IA lança um gasto que aparece no app na hora
        _entrar(pagina, "dono@teste.com")
        _esperar(lambda: pagina.locator("#sec-txt").count() == 1 and "Salário Marista" in _descricoes_app(pagina),
                 descricao="logar e baixar dados")
        pagina.fill("#sec-txt", "gastei 45 no ifood no nubank")
        pagina.press("#sec-txt", "Enter")
        _esperar(lambda: pagina.locator(".sec-desfazer").count() == 1, descricao="resposta com desfazer")
        resposta = pagina.locator(".sec-msg.assistant").last
        assert resposta.locator("b").inner_text() == "R$ 45,00"
        assert resposta.locator("img, script").count() == 0 and pagina.evaluate("window.__xss") is None
        _esperar(lambda: "iFood" in _descricoes_app(pagina), descricao="gasto chegar no app")

        # desfazer tira o gasto do Firebase e do app
        pagina.click(".sec-desfazer")
        _esperar(lambda: "iFood" not in _descricoes_firestore(secretaria), descricao="desfazer no Firebase")
        _esperar(lambda: "iFood" not in _descricoes_app(pagina), descricao="desfazer no app")
        assert "Desfeito" in pagina.text_content("#sec-hist")

        # histórico continua lá depois de recarregar
        pagina.reload()
        _esperar(lambda: "gastei 45 no ifood" in (pagina.text_content("#sec-hist") or ""), descricao="histórico")

        # tarefas na mão
        pagina.goto(f"{servidor_api}/app/?emulador#tarefas")
        _esperar(lambda: pagina.locator("#tar-nova").count() == 1, descricao="tela de tarefas")
        pagina.fill("#tar-nova", "Comprar pão")
        pagina.press("#tar-nova", "Enter")
        _esperar(lambda: "Comprar pão" in (pagina.text_content("#tar-lista") or ""), descricao="criar tarefa")
        pagina.click(".tar-check")
        _esperar(lambda: "Comprar pão" not in (pagina.text_content("#tar-lista") or ""), descricao="concluir tarefa")

        # chave da IA trocada pelo próprio app, conferida antes de guardar
        pagina.goto(f"{servidor_api}/app/?emulador#config")
        _esperar(lambda: pagina.locator("#ia-key-input").count() == 1, descricao="tela de config")
        assert "Espaço de trabalho padrão" in pagina.text_content("#tela-config")
        salvar = "button[onclick='Telas.config.salvarChaveIA()']"
        pagina.fill("#ia-key-input", "sk-ant-api03-ruim")
        pagina.click(salvar)
        _esperar(lambda: "não reconheceu" in (pagina.text_content("#toast") or ""), descricao="recusar chave errada")
        assert repo.estado_get("anthropic_chave") is None
        pagina.fill("#ia-key-input", "sk-ant-api03-boa")
        pagina.click(salvar)
        _esperar(lambda: "aceitou" in (pagina.text_content("#toast") or ""), descricao="aceitar chave boa")
        assert repo.estado_get("anthropic_chave") == "sk-ant-api03-boa"

        # abrir o app nunca passa pelo service worker: a página vem direto do servidor, como HTML
        resposta = pagina.reload()
        assert not resposta.from_service_worker and resposta.headers["content-type"].startswith("text/html")
        assert pagina.evaluate("caches.keys()") == []                       # nenhuma cópia guardada
        assert downloads == []
        navegador.close()
