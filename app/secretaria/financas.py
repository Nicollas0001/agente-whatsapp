"""Finanças: lê e grava no mesmo documento do app financas.html (financas/dados).

Os cálculos (saldo, fatura, resumo do mês) são cópia fiel dos do app (objeto
`Fin` no financas.html), para a secretária e o app mostrarem os mesmos números.
Toda gravação é uma transação que incrementa `rev`; o app usa esse número para
juntar as mudanças dos dois lados sem apagar nada.
"""
import calendar
import threading
import unicodedata
import uuid
from datetime import date, timedelta
from typing import Callable, Optional

from google.cloud import firestore

from app.secretaria import banco, diario, repositorio as repo

TIPOS_CONTA = ("corrente", "poupança", "investimento", "dinheiro")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _ref():
    return banco.cliente().collection("financas").document("dados")


def carregar() -> dict:
    snap = _ref().get()
    return (snap.to_dict() or {}) if snap.exists else {}


@firestore.transactional
def _aplicar(transacao, ref, funcao: Callable[[dict], tuple], tentativa: dict):
    snap = ref.get(transaction=transacao)
    dados = (snap.to_dict() or {}) if snap.exists else {}
    mudancas, resultado = funcao(dados)
    tentativa.update(antes=dados, mudancas=mudancas)  # a última tentativa é a que foi gravada
    if mudancas:
        mudancas = {**mudancas, "rev": (dados.get("rev") or 0) + 1,
                    "updatedAt": firestore.SERVER_TIMESTAMP, "alteradoPor": "secretaria"}
        if snap.exists:
            transacao.update(ref, mudancas)
        else:
            transacao.set(ref, mudancas)
    return resultado


_gravando = threading.Lock()


def alterar(funcao: Callable[[dict], tuple]):
    """Lê o documento, aplica `funcao(dados) -> (campos_alterados, resultado)` e grava, tudo atômico.

    A trava evita que a própria secretária dispute o documento consigo mesma;
    a transação cuida da disputa com o app (com mais tentativas que o padrão)."""
    with _gravando:
        tentativa = {}
        resultado = _aplicar(banco.cliente().transaction(max_attempts=15), _ref(), funcao, tentativa)
    if tentativa.get("mudancas") and diario.ativo() is not None:
        diario.ativo().anotar_financas(tentativa["antes"], tentativa["mudancas"])
    return resultado


# ---------- utilidades ----------

def moeda(valor: float) -> str:
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-R$ {texto}" if valor < 0 else f"R$ {texto}"


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def _achar(lista: list, alvo, rotulo: str, campo: str = "nome", filtro=None) -> dict:
    """Encontra por id ou pelo nome (sem acento, aceita pedaço). Ambíguo ou ausente vira erro explicativo."""
    itens = [i for i in lista if not filtro or filtro(i)]
    if alvo in (None, ""):
        raise ValueError(f"{rotulo} não informado(a)")
    alvo_txt = str(alvo).strip()
    for i in itens:
        if i.get("id") == alvo_txt:
            return i
    chave = _sem_acento(alvo_txt)
    exatos = [i for i in itens if _sem_acento(i.get(campo)) == chave]
    if len(exatos) == 1:
        return exatos[0]
    parecidos = [i for i in itens if chave and (chave in _sem_acento(i.get(campo)) or _sem_acento(i.get(campo)) in chave)]
    if len(parecidos) == 1:
        return parecidos[0]
    nomes = ", ".join(str(i.get(campo)) for i in (parecidos or itens)) or "(nenhum cadastrado)"
    if parecidos:
        raise ValueError(f"{rotulo} '{alvo}' é ambíguo(a); opções: {nomes}")
    raise ValueError(f"{rotulo} '{alvo}' não encontrado(a); existentes: {nomes}")


def _valor(v) -> float:
    if isinstance(v, str):
        v = v.replace("R$", "").strip()
        if "," in v:
            v = v.replace(".", "").replace(",", ".")
    numero = round(float(v), 2)
    if numero <= 0:
        raise ValueError("valor precisa ser maior que zero")
    return numero


def _data(v) -> str:
    return (repo.ler_data(v) or repo.hoje()).isoformat()


def _no_mes(data_txt: str, mes: int, ano: int) -> bool:
    return bool(data_txt) and data_txt[:7] == f"{ano:04d}-{mes:02d}"


def _add_mes(mes: int, ano: int, delta: int) -> tuple:
    total = ano * 12 + (mes - 1) + delta
    return total % 12 + 1, total // 12


def _ultimo_dia(mes: int, ano: int) -> int:
    return calendar.monthrange(ano, mes)[1]


def _curto(i: dict) -> str:
    return str(i.get("id", ""))[:6]


# ---------- cálculos (iguais ao app) ----------

def saldo_conta(d: dict, conta_id: str, ref: Optional[date] = None) -> float:
    conta = next((c for c in d.get("contas", []) if c.get("id") == conta_id), None)
    if not conta:
        return 0.0
    limite = (ref or repo.hoje()).isoformat()
    s = conta.get("saldoInicial") or 0
    for t in d.get("transacoes", []):
        if t.get("data", "") > limite:
            continue
        tipo, valor = t.get("tipo"), t.get("valor") or 0
        if t.get("contaId") == conta_id:
            if tipo == "receita":
                s += valor
            elif tipo in ("despesa", "fatura_cartao", "transferencia"):
                s -= valor
        if tipo == "transferencia" and t.get("contaDestinoId") == conta_id:
            s += valor
    return round(s, 2)


def periodo_fatura(cartao: dict, mes: int, ano: int) -> tuple:
    fd, dv = int(cartao.get("diaFechamento") or 1), int(cartao.get("diaVencimento") or 1)
    mes_ant, ano_ant = _add_mes(mes, ano, -1)
    ultimo_ant = _ultimo_dia(mes_ant, ano_ant)
    inicio = date(ano_ant, mes_ant, min(fd, ultimo_ant))
    ultimo_mes = _ultimo_dia(mes, ano)
    fim_dia = min(fd, ultimo_mes) - 1
    fim = date(ano_ant, mes_ant, ultimo_ant) if fim_dia < 1 else date(ano, mes, fim_dia)
    return inicio, fim, date(ano, mes, min(dv, ultimo_mes))


def fatura(d: dict, cartao_id: str, mes: int, ano: int) -> float:
    cartao = next((c for c in d.get("cartoes", []) if c.get("id") == cartao_id), None)
    if not cartao:
        return 0.0
    inicio, fim, _ = periodo_fatura(cartao, mes, ano)
    transacoes = d.get("transacoes", [])
    compras = sum(t.get("valor") or 0 for t in transacoes
                  if t.get("cartaoId") == cartao_id and t.get("tipo") == "despesa"
                  and inicio.isoformat() <= t.get("data", "") <= fim.isoformat())
    pagamentos = sum(t.get("valor") or 0 for t in transacoes
                     if t.get("tipo") == "fatura_cartao" and t.get("cartaoId") == cartao_id
                     and _no_mes(t.get("data", ""), mes, ano))
    return round(max(0.0, compras - pagamentos), 2)


def periodos_cartao(cartao: dict, ref: Optional[date] = None) -> tuple:
    """(mês da fatura fechada, mês da fatura aberta), como o app calcula."""
    ref = ref or repo.hoje()
    if ref.day >= int(cartao.get("diaFechamento") or 1):
        return (ref.month, ref.year), _add_mes(ref.month, ref.year, 1)
    return _add_mes(ref.month, ref.year, -1), (ref.month, ref.year)


def situacao_cartao(d: dict, cartao: dict, ref: Optional[date] = None) -> str:
    """Fatura fechada ainda a vencer (se houver valor) e a fatura aberta, com fechamento e vencimento."""
    ref = ref or repo.hoje()
    (mf, af), (ma, aa) = periodos_cartao(cartao, ref)
    partes = []
    _, _, venc_fechada = periodo_fatura(cartao, mf, af)
    valor_fechada = fatura(d, cartao["id"], mf, af)
    if valor_fechada > 0 and venc_fechada >= ref:
        partes.append(f"fatura fechada {moeda(valor_fechada)} vence {venc_fechada:%d/%m}")
    elif valor_fechada > 0:
        partes.append(f"fatura fechada {moeda(valor_fechada)} VENCIDA em {venc_fechada:%d/%m}")
    _, fim_aberta, venc_aberta = periodo_fatura(cartao, ma, aa)
    partes.append(f"fatura aberta {moeda(fatura(d, cartao['id'], ma, aa))} "
                  f"(fecha {fim_aberta + timedelta(days=1):%d/%m}, vence {venc_aberta:%d/%m})")
    return " · ".join(partes)


def comprometido(d: dict, cartao_id: str) -> float:
    transacoes = d.get("transacoes", [])
    compras = sum(t.get("valor") or 0 for t in transacoes if t.get("cartaoId") == cartao_id and t.get("tipo") == "despesa")
    pagos = sum(t.get("valor") or 0 for t in transacoes if t.get("cartaoId") == cartao_id and t.get("tipo") == "fatura_cartao")
    return round(max(0.0, compras - pagos), 2)


def gasto_categoria(d: dict, categoria_id: str, mes: int, ano: int) -> float:
    return round(sum(t.get("valor") or 0 for t in d.get("transacoes", [])
                     if t.get("tipo") == "despesa" and t.get("categoria") == categoria_id
                     and _no_mes(t.get("data", ""), mes, ano)), 2)


def fluxo_mes(d: dict, mes: int, ano: int) -> dict:
    entradas = saidas = despesas = 0.0
    for t in d.get("transacoes", []):
        if not _no_mes(t.get("data", ""), mes, ano):
            continue
        tipo, valor = t.get("tipo"), t.get("valor") or 0
        if tipo == "receita":
            entradas += valor
        if tipo == "despesa":
            despesas += valor
            if t.get("contaId") and not t.get("cartaoId"):
                saidas += valor
        if tipo == "fatura_cartao":
            saidas += valor
    return {"entradas": round(entradas, 2), "saidas": round(saidas, 2), "despesas": round(despesas, 2)}


def aportes_mes(d: dict, mes: int, ano: int) -> float:
    return round(sum(i.get("valor") or 0 for i in d.get("investimentos", []) if _no_mes(i.get("data", ""), mes, ano)), 2)


def patrimonio(d: dict) -> float:
    aportes = sum(i.get("valor") or 0 for i in d.get("investimentos", []))
    contas_inv = sum(saldo_conta(d, c["id"]) for c in d.get("contas", []) if c.get("tipo") == "investimento")
    return round(aportes + contas_inv, 2)


def _mes_ano(mes: int, ano: int) -> str:
    return f"{mes}-{ano}"  # mesmo formato do fixosLog do app


def fixos_do_mes(d: dict, ref: Optional[date] = None) -> list:
    """Contas fixas ativas que ainda não foram lançadas neste mês."""
    ref = ref or repo.hoje()
    log = {(l.get("fixoId"), l.get("mesAno")) for l in d.get("fixosLog", [])}
    chave = _mes_ano(ref.month, ref.year)
    return sorted((f for f in d.get("fixos", []) if f.get("ativo", True) and (f.get("id"), chave) not in log),
                  key=lambda f: int(f.get("dia") or 1))


def _nome(lista: list, item_id) -> str:
    return next((i.get("nome") for i in lista if i.get("id") == item_id), "?") if item_id else ""


def _linha_transacao(d: dict, t: dict) -> str:
    cat = next((c for c in d.get("categorias", []) if c.get("id") == t.get("categoria")), None)
    onde = _nome(d.get("cartoes", []), t.get("cartaoId")) or _nome(d.get("contas", []), t.get("contaId"))
    if t.get("tipo") == "transferencia":
        onde = f"{_nome(d.get('contas', []), t.get('contaId'))} → {_nome(d.get('contas', []), t.get('contaDestinoId'))}"
    parcela = f" {t.get('parcelaAtual')}/{t.get('parcelas')}" if (t.get("parcelas") or 1) > 1 else ""
    dia = t.get("data", "")
    return (f"[{_curto(t)}] {dia[8:10]}/{dia[5:7]} {t.get('tipo')} {moeda(t.get('valor') or 0)} "
            f"{t.get('descricao', '')}{parcela} ({cat.get('nome') if cat else '-'}; {onde or 'sem conta'})")


# ---------- retrato para a IA ----------

def retrato(d: Optional[dict] = None, ref: Optional[date] = None) -> str:
    try:
        d = carregar() if d is None else d
    except Exception as erro:  # finanças fora do ar não podem derrubar a secretária
        return f"FINANÇAS: indisponível agora ({type(erro).__name__})."
    ref = ref or repo.hoje()
    if not d.get("contas") and not d.get("transacoes"):
        return ("FINANÇAS: nada cadastrado ainda. Para começar, cadastre as contas e cartões da pessoa "
                "(configurar_financas) e as contas fixas do mês (contas_fixas).")
    mes, ano = ref.month, ref.year
    linhas = ["FINANÇAS (mesmos dados do app financas.html):"]

    contas = d.get("contas", [])
    linhas.append("Contas: " + " · ".join(
        f"{c.get('nome')} ({c.get('tipo')}) {moeda(saldo_conta(d, c['id'], ref))}" for c in contas) if contas else "Contas: (nenhuma)")

    for c in d.get("cartoes", []):
        livre = (c.get("limite") or 0) - comprometido(d, c["id"])
        linhas.append(f"Cartão {c.get('nome')}: {situacao_cartao(d, c, ref)} · limite livre {moeda(livre)}")

    fluxo = fluxo_mes(d, mes, ano)
    linhas.append(f"{MESES[mes - 1].capitalize()} até agora: entradas {moeda(fluxo['entradas'])} · "
                  f"saídas de caixa {moeda(fluxo['saidas'])} · despesas (inclui cartão) {moeda(fluxo['despesas'])}")

    de_hoje = [t for t in d.get("transacoes", []) if t.get("data") == ref.isoformat()]
    gasto_hoje = sum(t.get("valor") or 0 for t in de_hoje if t.get("tipo") == "despesa")
    linhas.append(f"Hoje: {len(de_hoje)} lançamento(s), gastos {moeda(gasto_hoje)}")

    metas = d.get("metas") or {}
    categorias = d.get("categorias", [])
    gastos = []
    for c in categorias:
        if c.get("tipo") == "receita":
            continue
        gasto = gasto_categoria(d, c["id"], mes, ano)
        meta = float(metas.get(c["id"]) or 0)
        if gasto or meta:
            pct = f" ({round(gasto / meta * 100)}%{' ⚠' if gasto > meta else ''})" if meta else ""
            gastos.append((gasto, f"{c.get('nome')} {moeda(gasto)}" + (f" / meta {moeda(meta)}{pct}" if meta else "")))
    if gastos:
        linhas.append("Gasto por categoria no mês: " + " · ".join(t for _, t in sorted(gastos, reverse=True)))

    pendentes = fixos_do_mes(d, ref)
    if pendentes:
        itens = []
        for f in pendentes:
            dia = int(f.get("dia") or 1)
            situacao = "ATRASADA" if dia < ref.day else ("HOJE" if dia == ref.day else f"dia {dia:02d}")
            tipo = "a receber" if f.get("tipo") == "receita" else "a pagar"
            itens.append(f"[{_curto(f)}] {f.get('descricao')} {moeda(f.get('valor') or 0)} {tipo} {situacao}")
        linhas.append("Contas fixas ainda não lançadas no mês: " + " · ".join(itens))

    meta_inv = d.get("metaInvest") or 0
    linhas.append(f"Investimentos: aportes no mês {moeda(aportes_mes(d, mes, ano))}"
                  + (f" de meta {moeda(meta_inv)}" if meta_inv else "")
                  + f" · patrimônio investido {moeda(patrimonio(d))}")

    recentes = sorted((t for t in d.get("transacoes", []) if t.get("data", "") <= ref.isoformat()),
                      key=lambda t: t.get("data", ""), reverse=True)[:12]
    if recentes:
        linhas.append("Últimos lançamentos:\n" + "\n".join("  " + _linha_transacao(d, t) for t in recentes))

    linhas.append("Categorias: despesa: " + ", ".join(c["nome"] for c in categorias if c.get("tipo") != "receita")
                  + " | receita: " + ", ".join(c["nome"] for c in categorias if c.get("tipo") == "receita"))
    return "\n".join(linhas)


def resumo_telegram(d: Optional[dict] = None, ref: Optional[date] = None) -> str:
    """Resumo rápido em HTML, sem IA (comando /financas)."""
    import html
    d = carregar() if d is None else d
    ref = ref or repo.hoje()
    if not d.get("contas"):
        return "Ainda não tem conta cadastrada. Me fala seus bancos e cartões que eu cadastro."
    saida = ["<b>Saldos</b>"]
    saida += [f"• {html.escape(c.get('nome', ''))}: {moeda(saldo_conta(d, c['id'], ref))}" for c in d.get("contas", [])]
    if d.get("cartoes"):
        saida.append("\n<b>Cartões</b>")
        for c in d["cartoes"]:
            saida.append(f"• {html.escape(c.get('nome', ''))}: {situacao_cartao(d, c, ref)}")
    fluxo = fluxo_mes(d, ref.month, ref.year)
    saida.append(f"\n<b>{MESES[ref.month - 1].capitalize()}</b>\n• Entrou {moeda(fluxo['entradas'])}\n"
                 f"• Gastou {moeda(fluxo['despesas'])}")
    pendentes = [f for f in fixos_do_mes(d, ref) if f.get("tipo") != "receita"]
    if pendentes:
        saida.append("\n<b>Contas fixas a lançar</b>")
        saida += [f"• {html.escape(f.get('descricao', ''))} {moeda(f.get('valor') or 0)} dia {int(f.get('dia') or 1):02d}"
                  for f in pendentes]
    meta_inv = d.get("metaInvest") or 0
    saida.append(f"\n<b>Investimentos</b>\n• Aportes no mês {moeda(aportes_mes(d, ref.month, ref.year))}"
                 + (f" de {moeda(meta_inv)}" if meta_inv else "") + f"\n• Patrimônio {moeda(patrimonio(d))}")
    return "\n".join(saida)


# ---------- gravação ----------

def _nova_transacao(**campos) -> dict:
    base = {"id": str(uuid.uuid4()), "data": repo.hoje().isoformat(), "descricao": "", "valor": 0.0,
            "tipo": "despesa", "categoria": None, "contaId": None, "cartaoId": None, "contaDestinoId": None,
            "parcelas": 1, "parcelaAtual": 1, "parcelaGrupoId": None}
    base.update(campos)
    return base


def _avisos_pos_gasto(d: dict, categorias_tocadas: set, cartoes_tocados: set, contas_tocadas: set, ref: date) -> list:
    avisos = []
    metas = d.get("metas") or {}
    for cat_id in categorias_tocadas:
        meta = float(metas.get(cat_id) or 0)
        if not meta:
            continue
        gasto = gasto_categoria(d, cat_id, ref.month, ref.year)
        nome = _nome(d.get("categorias", []), cat_id)
        if gasto > meta:
            avisos.append(f"ALERTA: {nome} passou da meta do mês ({moeda(gasto)} de {moeda(meta)})")
        elif gasto >= 0.8 * meta:
            avisos.append(f"Atenção: {nome} já usou {round(gasto / meta * 100)}% da meta ({moeda(gasto)} de {moeda(meta)})")
    for cartao_id in cartoes_tocados:
        cartao = next((c for c in d.get("cartoes", []) if c["id"] == cartao_id), {})
        livre = (cartao.get("limite") or 0) - comprometido(d, cartao_id)
        if cartao.get("limite") and livre < 0.1 * cartao["limite"]:
            avisos.append(f"Atenção: limite livre do {cartao.get('nome')} é {moeda(livre)}")
    for conta_id in contas_tocadas:
        saldo = saldo_conta(d, conta_id, ref)
        if saldo < 0:
            avisos.append(f"Atenção: {_nome(d.get('contas', []), conta_id)} ficou negativa ({moeda(saldo)})")
    return avisos


def lancar(itens: list) -> str:
    """Registra despesas, receitas, transferências (inclusive saques), pagamento de fatura e aportes/resgates."""
    if not itens:
        raise ValueError("nenhum lançamento informado")
    ref = repo.hoje()

    def aplicar(d):
        transacoes = list(d.get("transacoes", []))
        investimentos = list(d.get("investimentos", []))
        contas, cartoes, categorias = d.get("contas", []), d.get("cartoes", []), d.get("categorias", [])
        feitos, cats, carts, cts = [], set(), set(), set()
        for item in itens:
            tipo = item.get("tipo")
            valor = _valor(item.get("valor"))
            data = _data(item.get("data"))
            descricao = (item.get("descricao") or "").strip()
            if tipo in ("aporte", "resgate"):
                sinal = 1 if tipo == "aporte" else -1
                investimentos.append({"id": str(uuid.uuid4()), "data": data,
                                      "descricao": descricao or ("Investimento" if sinal > 0 else "Resgate"),
                                      "valor": sinal * valor})
                feitos.append(f"{tipo} {moeda(valor)} ({descricao or 'investimento'})")
                continue
            if tipo == "despesa":
                cat = _achar(categorias, item.get("categoria"), "categoria", filtro=lambda c: c.get("tipo") != "receita")
                cartao = _achar(cartoes, item["cartao"], "cartão") if item.get("cartao") else None
                conta = _achar(contas, item["conta"], "conta") if item.get("conta") and not cartao else None
                if not cartao and not conta:
                    raise ValueError(f"despesa '{descricao}': diga de qual conta ou cartão saiu")
                parcelas = max(1, int(item.get("parcelas") or 1))
                grupo = str(uuid.uuid4()) if parcelas > 1 else None
                ano, mes, dia = (int(x) for x in data.split("-"))
                for p in range(1, parcelas + 1):
                    m, a = _add_mes(mes, ano, p - 1)
                    transacoes.append(_nova_transacao(
                        data=date(a, m, min(dia, _ultimo_dia(m, a))).isoformat(), descricao=descricao or cat["nome"],
                        valor=round(valor / parcelas, 2), tipo="despesa", categoria=cat["id"],
                        contaId=conta["id"] if conta else None, cartaoId=cartao["id"] if cartao else None,
                        parcelas=parcelas, parcelaAtual=p, parcelaGrupoId=grupo))
                cats.add(cat["id"])
                (carts if cartao else cts).add((cartao or conta)["id"])
                onde = cartao["nome"] if cartao else conta["nome"]
                feitos.append(f"despesa {moeda(valor)} {descricao} ({cat['nome']}, {onde})"
                              + (f" em {parcelas}x de {moeda(round(valor / parcelas, 2))}" if parcelas > 1 else "")
                              + f" [{transacoes[-parcelas]['id'][:6]}]")
            elif tipo == "receita":
                cat = _achar(categorias, item.get("categoria"), "categoria", filtro=lambda c: c.get("tipo") == "receita")
                conta = _achar(contas, item.get("conta"), "conta")
                transacoes.append(_nova_transacao(data=data, descricao=descricao or cat["nome"], valor=valor,
                                                  tipo="receita", categoria=cat["id"], contaId=conta["id"]))
                feitos.append(f"receita {moeda(valor)} {descricao} ({cat['nome']}, {conta['nome']}) [{transacoes[-1]['id'][:6]}]")
            elif tipo == "transferencia":
                origem = _achar(contas, item.get("conta"), "conta de origem")
                destino = _achar(contas, item.get("conta_destino"), "conta de destino")
                if origem["id"] == destino["id"]:
                    raise ValueError("origem e destino são a mesma conta")
                transacoes.append(_nova_transacao(data=data, descricao=descricao or f"Transferência para {destino['nome']}",
                                                  valor=valor, tipo="transferencia", contaId=origem["id"],
                                                  contaDestinoId=destino["id"]))
                cts.add(origem["id"])
                feitos.append(f"transferência {moeda(valor)} {origem['nome']} → {destino['nome']} [{transacoes[-1]['id'][:6]}]")
            elif tipo == "fatura_cartao":
                cartao = _achar(cartoes, item.get("cartao"), "cartão")
                conta = _achar(contas, item.get("conta"), "conta")
                transacoes.append(_nova_transacao(data=data, descricao=descricao or f"Pagamento de fatura {cartao['nome']}",
                                                  valor=valor, tipo="fatura_cartao", contaId=conta["id"],
                                                  cartaoId=cartao["id"]))
                cts.add(conta["id"])
                feitos.append(f"fatura {cartao['nome']} paga {moeda(valor)} pela {conta['nome']} [{transacoes[-1]['id'][:6]}]")
            else:
                raise ValueError(f"tipo de lançamento inválido: {tipo}")
        novo = {**d, "transacoes": transacoes, "investimentos": investimentos}
        avisos = _avisos_pos_gasto(novo, cats, carts, cts, ref)
        mudancas = {"transacoes": transacoes}
        if investimentos != d.get("investimentos", []):
            mudancas["investimentos"] = investimentos
        return mudancas, "lançado: " + "; ".join(feitos) + ("\n" + "\n".join(avisos) if avisos else "")

    return alterar(aplicar)


def _achar_transacao(transacoes: list, ref_id: str) -> dict:
    ref_id = str(ref_id or "").strip().lstrip("[").rstrip("]")
    if len(ref_id) < 4:
        raise ValueError("informe o código do lançamento (os 6 caracteres entre colchetes)")
    achados = [t for t in transacoes if str(t.get("id", "")).startswith(ref_id)]
    if len(achados) != 1:
        raise ValueError(f"lançamento '{ref_id}' {'ambíguo' if achados else 'não encontrado'}; use consultar_financas")
    return achados[0]


def corrigir(ref_id: str, remover: bool = False, todas_parcelas: bool = False, campos: Optional[dict] = None) -> str:
    def aplicar(d):
        transacoes = list(d.get("transacoes", []))
        alvo = _achar_transacao(transacoes, ref_id)
        grupo = alvo.get("parcelaGrupoId") if todas_parcelas else None
        afetadas = [t for t in transacoes if (grupo and t.get("parcelaGrupoId") == grupo) or t is alvo]
        if remover:
            restantes = [t for t in transacoes if not any(t is a for a in afetadas)]
            return {"transacoes": restantes}, f"removido(s) {len(afetadas)} lançamento(s): {alvo.get('descricao')}"
        mudou = []
        for t in afetadas:
            novo = dict(t)
            for chave, valor in (campos or {}).items():
                if valor in (None, ""):
                    continue
                if chave == "valor":
                    novo["valor"] = _valor(valor)
                elif chave == "data" and t is alvo:
                    novo["data"] = _data(valor)
                elif chave == "descricao":
                    novo["descricao"] = str(valor).strip()
                elif chave == "categoria":
                    tipo_cat = "receita" if t.get("tipo") == "receita" else "despesa"
                    novo["categoria"] = _achar(d.get("categorias", []), valor, "categoria",
                                               filtro=lambda c: (c.get("tipo") == "receita") == (tipo_cat == "receita"))["id"]
                elif chave == "conta":
                    novo["contaId"] = _achar(d.get("contas", []), valor, "conta")["id"]
                    novo["cartaoId"] = None if t.get("tipo") == "despesa" else t.get("cartaoId")
                elif chave == "cartao":
                    novo["cartaoId"] = _achar(d.get("cartoes", []), valor, "cartão")["id"]
                    if t.get("tipo") == "despesa":
                        novo["contaId"] = None
            transacoes[transacoes.index(t)] = novo
            mudou.append(novo)
        if not mudou or all(m == a for m, a in zip(mudou, afetadas)):
            raise ValueError("nada para corrigir")
        return {"transacoes": transacoes}, "corrigido: " + _linha_transacao(d, mudou[0])

    return alterar(aplicar)


def consultar(inicio=None, fim=None, termo: str = "", categoria: str = "", conta: str = "",
              cartao: str = "", tipo: str = "", limite: int = 60) -> str:
    d = carregar()
    ref = repo.hoje()
    inicio = repo.ler_data(inicio) or ref.replace(day=1)
    fim = repo.ler_data(fim) or ref
    cat = _achar(d.get("categorias", []), categoria, "categoria") if categoria else None
    ct = _achar(d.get("contas", []), conta, "conta") if conta else None
    cc = _achar(d.get("cartoes", []), cartao, "cartão") if cartao else None
    termo = _sem_acento(termo)
    achadas = []
    for t in d.get("transacoes", []):
        if not (inicio.isoformat() <= t.get("data", "") <= fim.isoformat()):
            continue
        if tipo and t.get("tipo") != tipo:
            continue
        if cat and t.get("categoria") != cat["id"]:
            continue
        if ct and ct["id"] not in (t.get("contaId"), t.get("contaDestinoId")):
            continue
        if cc and t.get("cartaoId") != cc["id"]:
            continue
        if termo and termo not in _sem_acento(t.get("descricao")):
            continue
        achadas.append(t)
    achadas.sort(key=lambda t: t.get("data", ""))
    totais = {}
    for t in achadas:
        totais[t.get("tipo")] = totais.get(t.get("tipo"), 0) + (t.get("valor") or 0)
    cabecalho = (f"{len(achadas)} lançamento(s) de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}; totais: "
                 + (", ".join(f"{k} {moeda(v)}" for k, v in totais.items()) or "nenhum"))
    corpo = "\n".join(_linha_transacao(d, t) for t in achadas[-limite:])
    if len(achadas) > limite:
        corpo = f"(mostrando os {limite} mais recentes)\n" + corpo
    aportes = [i for i in d.get("investimentos", []) if inicio.isoformat() <= i.get("data", "") <= fim.isoformat()]
    if aportes and not (categoria or conta or cartao or termo or tipo):
        corpo += "\nInvestimentos no período: " + "; ".join(
            f"{i['data'][8:10]}/{i['data'][5:7]} {i.get('descricao')} {moeda(i.get('valor') or 0)}" for i in aportes)
    return cabecalho + ("\n" + corpo if corpo else "")


def contas_fixas(acao: str, dados: dict) -> str:
    def aplicar(d):
        fixos = [dict(f) for f in d.get("fixos", [])]
        if acao == "criar":
            tipo = "receita" if dados.get("tipo") == "receita" else "despesa"
            categoria = None
            if dados.get("categoria"):
                categoria = _achar(d.get("categorias", []), dados["categoria"], "categoria",
                                   filtro=lambda c: (c.get("tipo") == "receita") == (tipo == "receita"))["id"]
            cartao = _achar(d.get("cartoes", []), dados["cartao"], "cartão") if dados.get("cartao") else None
            conta = _achar(d.get("contas", []), dados["conta"], "conta") if dados.get("conta") and not cartao else None
            dia = int(dados.get("dia") or 0)
            if not 1 <= dia <= 31:
                raise ValueError("dia do vencimento precisa ser de 1 a 31")
            novo = {"id": str(uuid.uuid4()), "descricao": str(dados.get("descricao") or "").strip(),
                    "valor": _valor(dados.get("valor")), "dia": dia, "tipo": tipo,
                    "contaId": conta["id"] if conta else None, "cartaoId": cartao["id"] if cartao else None,
                    "categoria": categoria, "ativo": True}
            if not novo["descricao"]:
                raise ValueError("conta fixa sem descrição")
            fixos.append(novo)
            return {"fixos": fixos}, f"conta fixa criada [{_curto(novo)}] {novo['descricao']} {moeda(novo['valor'])} todo dia {dia}"

        alvo = _achar(fixos, dados.get("id") or dados.get("descricao"), "conta fixa", campo="descricao")
        if acao == "atualizar":
            if dados.get("valor") not in (None, ""):
                alvo["valor"] = _valor(dados["valor"])
            if dados.get("dia"):
                alvo["dia"] = int(dados["dia"])
            if dados.get("descricao_nova"):
                alvo["descricao"] = str(dados["descricao_nova"]).strip()
            if dados.get("categoria"):
                alvo["categoria"] = _achar(d.get("categorias", []), dados["categoria"], "categoria")["id"]
            if dados.get("cartao"):
                alvo["cartaoId"], alvo["contaId"] = _achar(d.get("cartoes", []), dados["cartao"], "cartão")["id"], None
            elif dados.get("conta"):
                alvo["contaId"], alvo["cartaoId"] = _achar(d.get("contas", []), dados["conta"], "conta")["id"], None
            return {"fixos": fixos}, f"conta fixa atualizada: {alvo['descricao']} {moeda(alvo['valor'])} dia {alvo['dia']}"
        if acao in ("desativar", "reativar"):
            alvo["ativo"] = acao == "reativar"
            return {"fixos": fixos}, f"conta fixa {alvo['descricao']} {'reativada' if alvo['ativo'] else 'desativada'}"
        if acao == "pagar":
            ref = repo.ler_data(dados.get("data")) or repo.hoje()
            if dados.get("mes_referencia"):
                ano, mes = (int(x) for x in str(dados["mes_referencia"])[:7].split("-"))
            else:
                mes, ano = ref.month, ref.year
            chave = _mes_ano(mes, ano)
            log = list(d.get("fixosLog", []))
            if any(l.get("fixoId") == alvo["id"] and l.get("mesAno") == chave for l in log):
                return {}, f"{alvo['descricao']} já estava lançada em {MESES[mes - 1]} (pode ter sido o app, no dia do vencimento)"
            valor = _valor(dados["valor"]) if dados.get("valor") not in (None, "") else alvo["valor"]
            conta_id, cartao_id = alvo.get("contaId"), alvo.get("cartaoId")
            if dados.get("cartao"):
                cartao_id, conta_id = _achar(d.get("cartoes", []), dados["cartao"], "cartão")["id"], None
            elif dados.get("conta"):
                conta_id, cartao_id = _achar(d.get("contas", []), dados["conta"], "conta")["id"], None
            if not conta_id and not cartao_id:
                raise ValueError(f"de qual conta ou cartão saiu {alvo['descricao']}?")
            transacao = _nova_transacao(data=ref.isoformat(), descricao=alvo["descricao"], valor=valor,
                                        tipo=alvo.get("tipo") or "despesa", categoria=alvo.get("categoria"),
                                        contaId=conta_id, cartaoId=cartao_id)
            log.append({"fixoId": alvo["id"], "mesAno": chave})
            transacoes = list(d.get("transacoes", [])) + [transacao]
            novo = {**d, "transacoes": transacoes}
            avisos = _avisos_pos_gasto(novo, {alvo.get("categoria")} - {None}, {cartao_id} - {None},
                                       {conta_id} - {None}, ref)
            verbo = "recebida" if alvo.get("tipo") == "receita" else "paga"
            return ({"transacoes": transacoes, "fixosLog": log},
                    f"{alvo['descricao']} {verbo} ({moeda(valor)}) [{transacao['id'][:6]}]"
                    + ("\n" + "\n".join(avisos) if avisos else ""))
        raise ValueError(f"ação inválida: {acao}")

    return alterar(aplicar)


def configurar(acao: str, dados: dict) -> str:
    def aplicar(d):
        if acao == "meta_categoria":
            cat = _achar(d.get("categorias", []), dados.get("categoria"), "categoria",
                         filtro=lambda c: c.get("tipo") != "receita")
            metas = dict(d.get("metas") or {})
            valor = float(dados.get("valor") or 0)
            if valor > 0:
                metas[cat["id"]] = round(valor, 2)
            else:
                metas.pop(cat["id"], None)
            return {"metas": metas}, (f"meta de {cat['nome']}: {moeda(valor)} por mês" if valor > 0
                                      else f"meta de {cat['nome']} removida")
        if acao == "meta_investimento":
            valor = round(float(dados.get("valor") or 0), 2)
            return {"metaInvest": valor}, f"meta de investimento: {moeda(valor)} por mês"
        if acao == "criar_conta":
            tipo = dados.get("tipo") or "corrente"
            if tipo not in TIPOS_CONTA:
                raise ValueError(f"tipo de conta inválido; use {', '.join(TIPOS_CONTA)}")
            nome = str(dados.get("nome") or "").strip()
            if not nome:
                raise ValueError("conta sem nome")
            if any(_sem_acento(c.get("nome")) == _sem_acento(nome) for c in d.get("contas", [])):
                raise ValueError(f"já existe uma conta chamada {nome}")
            conta = {"id": str(uuid.uuid4()), "nome": nome, "banco": dados.get("banco") or nome, "tipo": tipo,
                     "saldoInicial": round(float(dados.get("saldo") or 0), 2), "cor": "#118AB2",
                     "criadaEm": f"{repo.hoje().isoformat()}T00:00:00.000Z"}
            return {"contas": list(d.get("contas", [])) + [conta]}, f"conta {nome} ({tipo}) criada com {moeda(conta['saldoInicial'])}"
        if acao == "criar_cartao":
            nome = str(dados.get("nome") or "").strip()
            fecha, vence = int(dados.get("dia_fechamento") or 0), int(dados.get("dia_vencimento") or 0)
            if not nome or not (1 <= fecha <= 31 and 1 <= vence <= 31):
                raise ValueError("cartão precisa de nome, dia de fechamento e dia de vencimento")
            cartao = {"id": str(uuid.uuid4()), "nome": nome, "banco": dados.get("banco") or nome,
                      "limite": round(float(dados.get("limite") or 0), 2), "diaFechamento": fecha,
                      "diaVencimento": vence, "cor": "#EF476F", "criadaEm": f"{repo.hoje().isoformat()}T00:00:00.000Z"}
            return {"cartoes": list(d.get("cartoes", [])) + [cartao]}, f"cartão {nome} criado (fecha {fecha}, vence {vence})"
        if acao == "limite_cartao":
            cartoes = [dict(c) for c in d.get("cartoes", [])]
            cartao = _achar(cartoes, dados.get("nome"), "cartão")
            cartao["limite"] = round(float(dados.get("limite") or 0), 2)
            return {"cartoes": cartoes}, f"limite do {cartao['nome']}: {moeda(cartao['limite'])}"
        if acao == "ajustar_saldo":
            contas = [dict(c) for c in d.get("contas", [])]
            conta = _achar(contas, dados.get("nome"), "conta")
            atual = saldo_conta(d, conta["id"])
            novo = round(float(dados.get("saldo")), 2)
            conta["saldoInicial"] = round((conta.get("saldoInicial") or 0) + (novo - atual), 2)
            return {"contas": contas}, f"saldo da {conta['nome']} ajustado de {moeda(atual)} para {moeda(novo)}"
        if acao == "criar_categoria":
            nome = str(dados.get("nome") or "").strip()
            tipo = "receita" if dados.get("tipo") == "receita" else "despesa"
            if not nome:
                raise ValueError("categoria sem nome")
            if any(_sem_acento(c.get("nome")) == _sem_acento(nome) for c in d.get("categorias", [])):
                raise ValueError(f"já existe a categoria {nome}")
            categoria = {"id": str(uuid.uuid4()), "nome": nome, "ico": dados.get("icone") or "📦",
                         "cor": "#888888", "tipo": tipo}
            return {"categorias": list(d.get("categorias", [])) + [categoria]}, f"categoria {nome} ({tipo}) criada"
        raise ValueError(f"ação inválida: {acao}")

    return alterar(aplicar)
