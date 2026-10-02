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

from app.secretaria import banco, financas as fin  # noqa: E402

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
