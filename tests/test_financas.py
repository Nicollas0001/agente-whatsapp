import json
import threading
from datetime import date
from pathlib import Path

import pytest

from app.secretaria import banco, financas as fin, rotinas, repositorio as repo

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def app_financas(relogio):
    """Coloca no Firestore os dados no formato real do app (dados_iniciais.json)."""
    dados = json.loads((RAIZ / "dados_iniciais.json").read_text())
    dados.pop("exportadoEm", None)
    dados.pop("config", None)
    dados.update({"versao": "v-app", "recompensas": [{"mesAno": "9-2026", "ok": True}], "rev": 7})
    banco.cliente().collection("financas").document("dados").set(dados)
    return dados


def _saldo(nome):
    d = fin.carregar()
    return fin.saldo_conta(d, next(c["id"] for c in d["contas"] if c["nome"] == nome))


def _cartao(nome):
    return next(c for c in fin.carregar()["cartoes"] if c["nome"] == nome)


# ---------- cálculos iguais aos do app ----------

@pytest.mark.sem_banco
@pytest.mark.parametrize("fecha,mes,ano,esperado", [
    (10, 10, 2026, (date(2026, 9, 10), date(2026, 10, 9), date(2026, 10, 15))),
    (1, 10, 2026, (date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 15))),    # fecha dia 1
    (31, 3, 2026, (date(2026, 2, 28), date(2026, 3, 30), date(2026, 3, 15))),   # fevereiro curto
    (10, 1, 2027, (date(2026, 12, 10), date(2027, 1, 9), date(2027, 1, 15))),   # virada de ano
])
def test_periodo_da_fatura(fecha, mes, ano, esperado):
    assert fin.periodo_fatura({"diaFechamento": fecha, "diaVencimento": 15}, mes, ano) == esperado


@pytest.mark.sem_banco
def test_moeda():
    assert fin.moeda(1234.5) == "R$ 1.234,50"
    assert fin.moeda(-0.18) == "-R$ 0,18"


def test_saldos_do_app(app_financas):
    assert _saldo("Bradesco") == 1928.81  # 460 + 580 + 888,81 de receitas
    assert _saldo("Inter") == 0.18
    assert "Bradesco (corrente) R$ 1.928,81" in fin.retrato()


# ---------- lançamentos ----------

def test_gasto_parcelado_no_cartao(app_financas):
    resultado = fin.lancar([{"tipo": "despesa", "valor": 1200, "descricao": "TV", "categoria": "outros",
                             "cartao": "nubank", "parcelas": 10, "data": "2026-01-31"}])
    assert "em 10x de R$ 120,00" in resultado
    tv = sorted((t for t in fin.carregar()["transacoes"] if t["descricao"] == "TV"), key=lambda t: t["parcelaAtual"])
    assert [t["data"] for t in tv[:3]] == ["2026-01-31", "2026-02-28", "2026-03-31"]  # dia 31 vira fim do mês
    assert {t["parcelas"] for t in tv} == {10} and len({t["parcelaGrupoId"] for t in tv}) == 1
    assert all(t["cartaoId"] == _cartao("Nubank")["id"] and t["contaId"] is None for t in tv)


def test_gasto_precisa_de_forma_de_pagamento_e_categoria_certa(app_financas):
    with pytest.raises(ValueError, match="de qual conta ou cartão"):
        fin.lancar([{"tipo": "despesa", "valor": 10, "categoria": "Outros"}])
    with pytest.raises(ValueError, match="não encontrado"):
        fin.lancar([{"tipo": "despesa", "valor": 10, "categoria": "Viagem", "conta": "Inter"}])
    with pytest.raises(ValueError, match="ambíguo"):
        fin.lancar([{"tipo": "despesa", "valor": 10, "categoria": "ção", "conta": "Inter"}])  # Alimentação ou Educação?
    assert fin.carregar()["rev"] == 7  # nada foi gravado


def test_entrada_saque_e_fatura_mexem_nos_saldos(app_financas):
    fin.configurar("criar_conta", {"nome": "Carteira", "tipo": "dinheiro"})
    fin.lancar([
        {"tipo": "receita", "valor": "3.200,00", "descricao": "Salário", "categoria": "salario", "conta": "inter"},
        {"tipo": "transferencia", "valor": 200, "descricao": "Saque", "conta": "Inter", "conta_destino": "Carteira"},
        {"tipo": "despesa", "valor": 300, "descricao": "Mercado", "categoria": "Alimentação", "cartao": "Nubank"},
        {"tipo": "fatura_cartao", "valor": 300, "cartao": "Nubank", "conta": "Inter"},
    ])
    assert _saldo("Inter") == round(0.18 + 3200 - 200 - 300, 2)
    assert _saldo("Carteira") == 200
    d = fin.carregar()
    assert fin.fatura(d, _cartao("Nubank")["id"], 10, 2026) == 0  # pagamento abate a fatura do mês


def test_aporte_e_resgate(app_financas):
    fin.lancar([{"tipo": "aporte", "valor": 500, "descricao": "CDB"},
                {"tipo": "resgate", "valor": 120, "descricao": "CDB"}])
    d = fin.carregar()
    assert [i["valor"] for i in d["investimentos"]] == [500, -120]
    assert fin.aportes_mes(d, 10, 2026) == 380
    assert fin.patrimonio(d) == round(380 + 31.95, 2)  # aportes + saldo da conta de investimento (BTG)


def test_alerta_de_meta_por_categoria(app_financas):
    assert "R$ 100,00 por mês" in fin.configurar("meta_categoria", {"categoria": "Alimentação", "valor": 100})
    atencao = fin.lancar([{"tipo": "despesa", "valor": 85, "categoria": "Alimentação", "conta": "Inter"}])
    assert "85% da meta" in atencao
    estourou = fin.lancar([{"tipo": "despesa", "valor": 30, "categoria": "Alimentação", "conta": "Inter"}])
    assert "ALERTA: Alimentação / Mercado passou da meta" in estourou
    assert "Inter ficou negativa" in estourou
    assert "meta R$ 100,00 (115% ⚠)" in fin.retrato()


def test_contas_fixas_pagar_uma_vez_por_mes(app_financas):
    criado = fin.contas_fixas("criar", {"descricao": "Aluguel", "valor": 800, "dia": 5, "categoria": "Aluguel",
                                        "conta": "Bradesco"})
    assert "todo dia 5" in criado
    assert "Aluguel R$ 800,00 a pagar ATRASADA" not in fin.retrato()  # dia 1: ainda não venceu
    assert "Aluguel R$ 800,00 a pagar dia 05" in fin.retrato()
    pago = fin.contas_fixas("pagar", {"descricao": "aluguel"})
    assert "Aluguel paga (R$ 800,00)" in pago
    d = fin.carregar()
    assert {"fixoId": d["fixos"][0]["id"], "mesAno": "10-2026"} in d["fixosLog"]  # mesmo formato do app
    assert "já estava lançada" in fin.contas_fixas("pagar", {"descricao": "Aluguel"})
    assert "Contas fixas ainda não lançadas" not in fin.retrato()
    assert _saldo("Bradesco") == round(1928.81 - 800, 2)


def test_corrigir_e_remover_parcelas(app_financas):
    resultado = fin.lancar([{"tipo": "despesa", "valor": 600, "descricao": "Fone", "categoria": "Outros",
                             "cartao": "Nubank", "parcelas": 3}])
    codigo = resultado.split("[")[1][:6]
    assert "R$ 250,00" in fin.corrigir(codigo, campos={"valor": 250})
    assert sorted(t["valor"] for t in fin.carregar()["transacoes"] if t["descricao"] == "Fone") == [200, 200, 250]
    assert "removido(s) 3" in fin.corrigir(codigo, remover=True, todas_parcelas=True)
    assert not [t for t in fin.carregar()["transacoes"] if t["descricao"] == "Fone"]


def test_consulta_por_termo_e_periodo(app_financas):
    fin.lancar([{"tipo": "despesa", "valor": 45.9, "descricao": "iFood almoço", "categoria": "Alimentação", "cartao": "Nubank"},
                {"tipo": "despesa", "valor": 30, "descricao": "iFood jantar", "categoria": "Alimentação", "cartao": "Nubank"},
                {"tipo": "despesa", "valor": 99, "descricao": "Farmácia", "categoria": "Saúde", "conta": "Inter"}])
    resposta = fin.consultar(termo="ifood")
    assert resposta.startswith("2 lançamento(s)") and "despesa R$ 75,90" in resposta
    assert "Salário Marista" in fin.consultar("2026-05-01", "2026-05-31", tipo="receita")


def test_configurar_contas_cartoes_e_saldo(app_financas):
    with pytest.raises(ValueError, match="já existe"):
        fin.configurar("criar_conta", {"nome": "inter"})
    fin.configurar("ajustar_saldo", {"nome": "Inter", "saldo": 1000})
    assert _saldo("Inter") == 1000
    fin.configurar("criar_cartao", {"nome": "C6", "limite": 2000, "dia_fechamento": 3, "dia_vencimento": 10})
    fin.configurar("limite_cartao", {"nome": "C6", "limite": 2500})
    assert _cartao("C6")["limite"] == 2500
    assert fin.configurar("meta_investimento", {"valor": 900}).endswith("R$ 900,00 por mês")


def test_gravacao_preserva_o_resto_e_avanca_a_revisao(app_financas):
    fin.lancar([{"tipo": "aporte", "valor": 100}])
    d = fin.carregar()
    assert (d["rev"], d["alteradoPor"], d["versao"], d["recompensas"]) == (8, "secretaria", "v-app", [{"mesAno": "9-2026", "ok": True}])
    assert len(d["transacoes"]) == len(app_financas["transacoes"])


def test_gravacoes_simultaneas_nao_se_perdem(app_financas):
    erros = []

    def lancar(i):
        try:
            fin.lancar([{"tipo": "aporte", "valor": 10 + i, "descricao": f"A{i}"}])
        except Exception as e:  # pragma: no cover - aparece no assert
            erros.append(e)

    threads = [threading.Thread(target=lancar, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    d = fin.carregar()
    assert not erros
    assert sorted(i["descricao"] for i in d["investimentos"]) == [f"A{i}" for i in range(5)]
    assert d["rev"] == 7 + 5


def test_resumo_financeiro_sem_ia(app_financas, telegram_falso):
    fin.contas_fixas("criar", {"descricao": "Internet", "valor": 99.9, "dia": 20, "conta": "Inter"})
    rotinas.ao_receber(repo.salvar_mensagem("user", "/financas", processada=False))
    texto = telegram_falso[0]["text"]
    assert "<b>Saldos</b>" in texto and "Bradesco: R$ 1.928,81" in texto
    assert "Nubank: fatura aberta" in texto and "Internet R$ 99,90 dia 20" in texto


def test_ferramentas_financeiras_pela_ia(app_financas):
    from app.secretaria import ia
    saida = ia.executar_ferramenta("lancar_financas", {"itens": [
        {"tipo": "despesa", "valor": 45, "descricao": "iFood", "categoria": "Alimentação / Mercado", "cartao": "Nubank"}]})
    codigo = saida.split("[")[1][:6]
    assert "iFood" in ia.executar_ferramenta("consultar_financas", {"termo": "ifood"})
    assert "R$ 50,00" in ia.executar_ferramenta("corrigir_lancamento", {"codigo": codigo, "valor": 50})
    assert "criada" in ia.executar_ferramenta("contas_fixas", {"acao": "criar", "descricao": "Netflix", "valor": 39.9,
                                                                "dia": 12, "cartao": "Nubank", "categoria": "Lazer"})
    assert "meta" in ia.executar_ferramenta("configurar_financas", {"acao": "meta_investimento", "valor": 900})
