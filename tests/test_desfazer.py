import json
from pathlib import Path

import pytest

from app.secretaria import banco, diario, financas as fin, ia, repositorio as repo

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def app_financas(relogio):
    dados = json.loads((RAIZ / "dados_iniciais.json").read_text())
    dados.pop("exportadoEm", None)
    dados.pop("config", None)
    dados["metaInvest"] = 900
    banco.cliente().collection("financas").document("dados").set(dados)
    return dados


def _descricoes():
    return {t["descricao"] for t in fin.carregar()["transacoes"]}


def test_desfaz_tarefas_lembretes_e_memoria(relogio):
    existente = repo.criar_tarefa({"titulo": "Mandar proposta", "area": "profissional", "agendada_para": "2026-10-01"})
    with diario.registrando("pacote de mudanças"):
        nova = repo.criar_tarefa({"titulo": "Ligar pro contador", "area": "profissional"})
        repo.atualizar_tarefa(existente, {"agendada_para": "2026-10-05", "prioridade": 1})
        repo.criar_lembrete("2026-10-01 15:00", "Buscar exame")
        repo.salvar_memoria("Trabalha das 8h às 17h")
    assert diario.desfazer_ultima() == "Desfeito: pacote de mudanças"
    assert repo.obter_tarefa(nova) is None
    volta = repo.obter_tarefa(existente)
    assert (volta.agendada_para.isoformat(), volta.prioridade, volta.adiamentos) == ("2026-10-01", 2, 0)
    assert repo.lembretes_futuros() == [] and repo.listar_memorias() == []
    assert diario.desfazer_ultima() == "Não tem nenhuma ação minha para desfazer."


def test_desfaz_conclusao_com_repeticao(relogio):
    tid = repo.criar_tarefa({"titulo": "Academia", "area": "pessoal", "recorrencia": "diaria", "agendada_para": "2026-10-01"})
    with diario.registrando("fiz a academia"):
        repo.concluir_tarefa(tid)
    assert len(repo.tarefas_abertas()) == 1 and repo.tarefas_abertas()[0].id != tid
    diario.desfazer_ultima()
    abertas = repo.tarefas_abertas()
    assert [t.id for t in abertas] == [tid] and abertas[0].status == "pendente"
    assert repo.registros_periodo(relogio.atual.date(), relogio.atual.date()) == []


def test_desfaz_financas_sem_apagar_o_que_veio_depois(app_financas):
    with diario.registrando("gastei e mudei a meta"):
        fin.lancar([{"tipo": "despesa", "valor": 45, "descricao": "iFood", "categoria": "Alimentação", "cartao": "Nubank"}])
        fin.configurar("meta_investimento", {"valor": 1500})
        codigo = next(t["id"] for t in fin.carregar()["transacoes"] if t["descricao"] == "Salário Marista")[:6]
        fin.corrigir(codigo, campos={"valor": 999})
        fin.corrigir(next(t["id"] for t in fin.carregar()["transacoes"] if t["descricao"] == "Mesada mãe")[:6], remover=True)
    # depois disso, a pessoa lança algo no app (fora do diário)
    fin.lancar([{"tipo": "despesa", "valor": 12, "descricao": "Padaria", "categoria": "Alimentação", "conta": "Inter"}])

    diario.desfazer_ultima()
    d = fin.carregar()
    assert "iFood" not in _descricoes() and "Padaria" in _descricoes() and "Mesada mãe" in _descricoes()
    assert next(t["valor"] for t in d["transacoes"] if t["descricao"] == "Salário Marista") == 888.81
    assert d["metaInvest"] == 900


def test_criado_e_apagado_na_mesma_rodada_nao_volta(app_financas):
    with diario.registrando("lancei e apaguei"):
        resultado = fin.lancar([{"tipo": "despesa", "valor": 30, "descricao": "Uber", "categoria": "Transporte", "conta": "Inter"}])
        fin.corrigir(resultado.split("[")[1][:6], remover=True)
    diario.desfazer_ultima()
    assert "Uber" not in _descricoes()


def test_desfazer_pela_ia_e_por_comando(app_financas):
    with diario.registrando("aporte"):
        ia.executar_ferramenta("lancar_financas", {"itens": [{"tipo": "aporte", "valor": 300}]})
    with diario.registrando("pedido de desfazer") as atual:
        assert ia.executar_ferramenta("desfazer_ultima_acao", {}) == "Desfeito: aporte"
    assert atual.vazio()  # desfazer não vira uma nova ação para desfazer
    assert fin.carregar()["investimentos"] == []
