"""Os testes rodam contra o emulador do Firestore:

    firebase emulators:start --only firestore --project demo-secretaria
    FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python -m pytest tests
"""
import os
from datetime import datetime

import pytest
import requests

os.environ["TELEGRAM_BOT_TOKEN"] = "token-de-teste"
os.environ["TELEGRAM_OWNER_ID"] = "42"
os.environ["SECRETARIA_ESPERA_AGRUPAR_SEG"] = "0"
os.environ.setdefault("FIREBASE_PROJECT_ID", "demo-secretaria")
os.environ.pop("CRON_SECRET", None)
os.environ.pop("FIREBASE_CREDENCIAIS", None)

from app.secretaria import repositorio, telegram  # noqa: E402

EMULADOR = os.getenv("FIRESTORE_EMULATOR_HOST")


def pytest_collection_modifyitems(config, items):
    if EMULADOR:
        return
    pular = pytest.mark.skip(reason="defina FIRESTORE_EMULATOR_HOST (emulador do Firestore)")
    for item in items:
        if "sem_banco" not in item.keywords:
            item.add_marker(pular)


@pytest.fixture(autouse=True)
def limpar_banco():
    if EMULADOR:
        requests.delete(f"http://{EMULADOR}/emulator/v1/projects/{os.environ['FIREBASE_PROJECT_ID']}"
                        "/databases/(default)/documents", timeout=10).raise_for_status()
    yield


@pytest.fixture
def relogio(monkeypatch):
    """Congela o "agora" da secretária. Use relogio.ajustar(datetime(...))."""
    class Relogio:
        atual = datetime(2026, 10, 1, 9, 0)  # quinta-feira

        def ajustar(self, momento):
            self.atual = momento

    r = Relogio()
    monkeypatch.setattr(repositorio, "agora", lambda: r.atual)
    return r


@pytest.fixture
def telegram_falso(monkeypatch):
    enviados = []

    def api(metodo, **dados):
        if metodo == "sendMessage":
            enviados.append(dados)
        return {"ok": True}

    monkeypatch.setattr(telegram, "_api", api)
    return enviados
