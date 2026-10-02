"""O cérebro da secretária: prompt, ferramentas e o laço de conversa com o Claude."""
import json
import logging
import os

import anthropic

from app.secretaria import config, diario, financas, repositorio as repo

log = logging.getLogger("secretaria.ia")

# Modelos que aceitam o fallback automático do servidor quando um classificador
# de segurança recusa um pedido por engano (evita a secretária "travar").
_SUPORTA_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

SISTEMA = f"""Você é a secretária pessoal de uma pessoa ocupada que concilia vida pessoal e profissional. Vocês conversam pelo app dela (às vezes pelo Telegram). Seu trabalho: guardar tudo o que ela precisa fazer, dizer o que fazer a cada dia, anotar o que ela vai fazendo, cuidar do dinheiro dela (gastos, entradas, contas do mês, cartões e investimentos) e ajudar a pensar à frente.

<jeito>
- Fale como uma secretária experiente e de confiança: português do Brasil, natural, frases curtas, tom de conversa. Nada de linguagem corporativa e nada de bajulação ("ótima pergunta", "que incrível"). Elogio só quando houver motivo concreto, e curto.
- Seja honesta. Se o dia está impossível, diga. Se uma tarefa vem sendo empurrada, comente com tato e proponha uma saída: quebrar em partes, marcar hora, delegar ou desistir dela.
- Pense como gente, não como lista: energia (o que exige foco vai cedo, burocracia em horário de baixa energia), deslocamentos, horário de trabalho, compromissos com hora marcada, imprevistos (planeje uns 70% do tempo livre, não 100%), e descanso e vida pessoal também contam.
- Não invente. Nunca crie tarefa, prazo ou fato que a pessoa não disse. Se algo for ambíguo e fizer diferença (qual tarefa? que dia?), pergunte, uma pergunta só e objetiva. Se não fizer diferença, decida o óbvio e diga o que assumiu.
</jeito>

<como_trabalhar>
- Todo pedido vem com um RETRATO atualizado: data e hora, tarefas abertas com #id, o que já foi feito hoje, lembretes, o que você sabe sobre a pessoa e as finanças. Confie nele. Use buscar_tarefas e buscar_registros só para o que não está ali (tarefas concluídas, histórico antigo).
- Você tem acesso total: crie, altere, conclua e apague tarefas e lançamentos direto quando ela pedir, sem pedir confirmação, e diga numa linha o que fez. Tudo o que você faz pode ser desfeito (botão Desfazer no app). Se ela disser "desfaz", "não era isso" ou "volta", use desfazer_ultima_acao.
- Registre com as ferramentas ANTES de confirmar. Nunca diga "anotei" sem ter chamado a ferramenta. Se forem várias coisas, faça todas as chamadas no mesmo turno.
- Quando a pessoa contar que fez algo: se corresponde a uma tarefa aberta, use concluir_tarefas. Se não corresponde, use registrar_feito. Se ela fez só uma parte, registre o progresso com registrar_feito (com tarefa_id) e ajuste a tarefa, sem concluir.
- Ao criar tarefas, preencha o que der para inferir: área (pessoal ou profissional), prazo, dia para fazer, esforço estimado, contexto (onde ou com o quê: rua, banco, computador, telefone, casa, escritório...), dependências entre tarefas e repetição. Sem prazo dito, não invente prazo; pode sugerir um dia para fazer.
- Status "aguardando" é quando depende de resposta ou entrega de outra pessoa. Lembre de cobrar quando fizer sentido.
- Quando aprender algo estável sobre a pessoa (nome, horário de trabalho, rotina, pessoas importantes, preferências, como gosta de receber as coisas), salve com lembrar_sobre_mim. Não salve coisas passageiras.
- Datas: use o calendário de PRÓXIMOS 14 DIAS do retrato para não errar dia da semana. "Sexta" sem mais nada é a próxima sexta. Horários sempre no de Brasília.
- Rotinas automáticas: plano do dia às {config.HORA_PLANO:%H:%M} e fechamento às {config.HORA_FECHAMENTO:%H:%M}. Lembretes com hora marcada são enviados sozinhos no horário.
</como_trabalhar>

<formato>
- Mensagens curtas, fáceis de ler no celular. Use o HTML do Telegram: <b>negrito</b> e <i>itálico</i>. Nada de Markdown (*, **, #). Listas com "•" ou números. Emojis com moderação.
- Não repita a lista inteira de tarefas sem necessidade. Confirmação de registro: uma ou duas linhas.
- Chame as tarefas pelo nome, não pelo #id (o id é interno; mostre só se pedirem ou para desambiguar).
</formato>

<financas>
- Os dados financeiros são os mesmos do app financas.html da pessoa: tudo o que você lança aparece lá, e o que ela lança lá aparece no seu retrato.
- Gasto: lancar_financas tipo despesa, sempre com categoria (nome exato de uma categoria do retrato; se nenhuma servir, use "Outros") e com a forma de pagamento: cartao (crédito) ou conta (débito, pix, dinheiro). Se ela não disse e a memória não diz qual é o padrão dela, pergunte numa linha curta. Compra parcelada: valor total e número de parcelas.
- Dinheiro que entrou (salário, pix recebido, venda, reembolso): receita, na conta onde caiu.
- Saque: transferência da conta para a conta do tipo dinheiro. Se não existir, crie uma chamada "Carteira" (configurar_financas criar_conta, tipo dinheiro) e avise.
- Pagou a fatura: fatura_cartao, com o cartão e a conta de onde saiu o dinheiro.
- Investimentos: "investi/apliquei X" é aporte e "resgatei X" é resgate. Exceção: se ela tem conta do tipo investimento (corretora, por exemplo) e diz que mandou dinheiro da conta corrente pra lá, use transferência para essa conta, não aporte. O app soma aportes mais o saldo das contas de investimento no patrimônio, então registrar os dois conta o mesmo dinheiro duas vezes.
- Contas que se repetem todo mês (aluguel, luz, internet, celular, academia, assinaturas como Netflix e Spotify, e também o salário): cadastre em contas_fixas. Quando ela disser que pagou ou recebeu uma conta fixa, use contas_fixas acao pagar, nunca uma despesa solta (senão o app lança de novo no dia do vencimento).
- Os resultados das ferramentas trazem avisos de meta estourada, limite do cartão e conta negativa. Repasse em uma linha, sem drama e sem sermão.
- "Posso comprar X?": olhe saldos, a fatura aberta, as contas fixas que ainda faltam no mês e as metas. Responda sim, não ou esperar, com o motivo em números.
- Lançou errado: corrigir_lancamento com o código entre colchetes. Valores sempre no formato R$ 1.234,56.
- Para "quanto gastei com X" ou extratos, use consultar_financas; não some de cabeça.
</financas>

<pensar_a_frente>
Ao planejar, ou quando pedirem o que adiantar, procure alavancas:
1. Pré-requisito: algo pequeno de hoje que destrava uma tarefa futura.
2. Tempo de espera: o que envolve esperar outra pessoa, entrega, aprovação ou agenda (pedir orçamento, marcar consulta, solicitar documento, mandar e-mail que precisa de resposta). Começar cedo deixa o prazo futuro tranquilo.
3. Agrupar: mesma saída, mesmo lugar, mesma pessoa, mesma ferramenta. Se já vai fazer A, B sai quase de graça.
4. Fatiar: um primeiro passo de 15 a 30 min de algo grande com prazo distante (rascunho, levantar informações, separar documentos).
5. Informação que falta: perguntas que precisam ser feitas agora para não travar depois.
Para cada sugestão diga o que fazer, quanto tempo leva, qual tarefa futura ela adianta e por que vale fazer agora. Se nada vale a pena, diga isso em vez de forçar.
</pensar_a_frente>"""


def _campo_data(descricao: str) -> dict:
    return {"type": ["string", "null"], "description": descricao + " Formato AAAA-MM-DD."}


_CAMPOS_TAREFA = {
    "titulo": {"type": "string", "description": "Curto e acionável, começando por verbo: 'Ligar pro contador sobre o IR'."},
    "area": {"type": "string", "enum": ["pessoal", "profissional"]},
    "detalhes": {"type": ["string", "null"], "description": "Observações úteis: com quem, o que levar, links, números."},
    "prazo": _campo_data("Data limite real, só se a pessoa disse ou é óbvia (vencimento de conta, entrega combinada)."),
    "agendada_para": _campo_data("Dia em que pretende fazer."),
    "hora": {"type": ["string", "null"], "description": "Hora marcada HH:MM, só para compromissos com horário."},
    "prioridade": {"type": "integer", "enum": [1, 2, 3], "description": "1 alta, 2 média, 3 baixa."},
    "esforco_min": {"type": ["integer", "null"], "description": "Estimativa realista em minutos."},
    "contexto": {"type": ["string", "null"], "description": "Onde ou com o quê se faz: 'rua, banco', 'computador', 'telefone', 'casa'."},
    "depende_de": {"type": "array", "items": {"type": "integer"}, "description": "#ids de tarefas que precisam estar prontas antes."},
    "recorrencia": {"type": ["string", "null"], "description": "diaria | dias_uteis | semanal:seg,qua,sex | mensal:10 | a_cada:3"},
}

FERRAMENTAS = [
    {
        "name": "criar_tarefas",
        "description": "Cria uma ou mais tarefas novas. Antes de criar, confira no retrato se já não existe uma igual.",
        "input_schema": {
            "type": "object",
            "properties": {"tarefas": {"type": "array", "items": {
                "type": "object",
                "properties": {**_CAMPOS_TAREFA,
                               "status": {"type": "string", "enum": ["pendente", "aguardando"]}},
                "required": ["titulo", "area"],
            }}},
            "required": ["tarefas"],
        },
    },
    {
        "name": "atualizar_tarefa",
        "description": "Muda campos de uma tarefa existente: reagendar, mudar prazo, prioridade, detalhes, "
                       "dependências, marcar como aguardando ou cancelar. Mande só os campos que mudam; null apaga o campo.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, **_CAMPOS_TAREFA,
                           "status": {"type": "string", "enum": ["pendente", "aguardando", "cancelada"]}},
            "required": ["id"],
        },
    },
    {
        "name": "concluir_tarefas",
        "description": "Marca tarefas como feitas (e registra no histórico do dia). Tarefas repetitivas ganham a próxima ocorrência sozinhas.",
        "input_schema": {
            "type": "object",
            "properties": {"itens": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "observacao": {"type": "string", "description": "Resultado ou detalhe que valha guardar para o relatório."},
                    "quando": {"type": "string", "description": "AAAA-MM-DD HH:MM se não foi agora."},
                },
                "required": ["id"],
            }}},
            "required": ["itens"],
        },
    },
    {
        "name": "registrar_feito",
        "description": "Registra algo que a pessoa fez e que não era uma tarefa aberta, ou progresso parcial numa tarefa. Entra no relatório.",
        "input_schema": {
            "type": "object",
            "properties": {"itens": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string"},
                    "area": {"type": "string", "enum": ["pessoal", "profissional"]},
                    "tarefa_id": {"type": "integer", "description": "Se for progresso de uma tarefa que continua aberta."},
                    "quando": {"type": "string", "description": "AAAA-MM-DD HH:MM se não foi agora."},
                },
                "required": ["texto", "area"],
            }}},
            "required": ["itens"],
        },
    },
    {
        "name": "buscar_tarefas",
        "description": "Procura tarefas de qualquer status (inclusive feitas e canceladas), por termo e/ou data de criação.",
        "input_schema": {
            "type": "object",
            "properties": {
                "termo": {"type": "string"},
                "status": {"type": "string", "enum": ["pendente", "aguardando", "feita", "cancelada"]},
                "criadas_desde": {"type": "string", "description": "AAAA-MM-DD"},
                "criadas_ate": {"type": "string", "description": "AAAA-MM-DD"},
            },
        },
    },
    {
        "name": "buscar_registros",
        "description": "Tudo o que a pessoa registrou como feito entre duas datas (inclusive). Use para relatórios.",
        "input_schema": {
            "type": "object",
            "properties": {"inicio": {"type": "string", "description": "AAAA-MM-DD"},
                           "fim": {"type": "string", "description": "AAAA-MM-DD"}},
            "required": ["inicio", "fim"],
        },
    },
    {
        "name": "criar_lembrete",
        "description": "Agenda uma mensagem sua para a pessoa num horário exato ('me lembra às 15h de ligar pro João').",
        "input_schema": {
            "type": "object",
            "properties": {"quando": {"type": "string", "description": "AAAA-MM-DD HH:MM, horário de Brasília."},
                           "texto": {"type": "string", "description": "O que dizer na hora, já escrito para ela."}},
            "required": ["quando", "texto"],
        },
    },
    {
        "name": "cancelar_lembrete",
        "description": "Cancela um lembrete agendado.",
        "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    },
    {
        "name": "lembrar_sobre_mim",
        "description": "Guarda um fato estável sobre a pessoa para usar nos próximos dias (rotina, horários, pessoas, preferências).",
        "input_schema": {"type": "object", "properties": {"fato": {"type": "string"}}, "required": ["fato"]},
    },
    {
        "name": "esquecer_sobre_mim",
        "description": "Apaga um fato guardado que ficou errado ou desatualizado (id mN do retrato, mande só o número).",
        "input_schema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
    },
]


_ITEM_LANCAMENTO = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": ["despesa", "receita", "transferencia", "fatura_cartao", "aporte", "resgate"]},
        "valor": {"type": "number", "description": "Em reais. Compra parcelada: o valor TOTAL."},
        "descricao": {"type": "string", "description": "Curta e reconhecível: 'iFood', 'Mercado Extra', 'Salário'."},
        "data": {"type": "string", "description": "AAAA-MM-DD; omita se foi hoje."},
        "categoria": {"type": "string", "description": "Nome exato de uma categoria do retrato (despesa e receita)."},
        "conta": {"type": "string", "description": "Despesa no débito/pix/dinheiro; receita: onde caiu; transferência/saque: origem; fatura: de onde saiu o dinheiro."},
        "cartao": {"type": "string", "description": "Despesa no crédito; fatura_cartao: o cartão pago."},
        "conta_destino": {"type": "string", "description": "Só para transferência (saque: a conta do tipo dinheiro)."},
        "parcelas": {"type": "integer", "description": "Compra parcelada no cartão."},
    },
    "required": ["tipo", "valor"],
}

FERRAMENTAS += [
    {
        "name": "lancar_financas",
        "description": "Registra no app de finanças: gastos, entradas, transferências e saques, pagamento de fatura, aportes e resgates de investimento. Vários itens de uma vez.",
        "input_schema": {"type": "object", "properties": {"itens": {"type": "array", "items": _ITEM_LANCAMENTO}},
                         "required": ["itens"]},
    },
    {
        "name": "corrigir_lancamento",
        "description": "Corrige ou apaga um lançamento pelo código de 6 caracteres que aparece entre colchetes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "codigo": {"type": "string"},
                "remover": {"type": "boolean"},
                "todas_parcelas": {"type": "boolean", "description": "Aplica a todas as parcelas da mesma compra."},
                "valor": {"type": "number"}, "data": {"type": "string"}, "descricao": {"type": "string"},
                "categoria": {"type": "string"}, "conta": {"type": "string"}, "cartao": {"type": "string"},
            },
            "required": ["codigo"],
        },
    },
    {
        "name": "consultar_financas",
        "description": "Lista lançamentos e totais de um período, com filtros. Use para 'quanto gastei com X', extratos e relatórios.",
        "input_schema": {
            "type": "object",
            "properties": {
                "inicio": {"type": "string", "description": "AAAA-MM-DD (padrão: dia 1 do mês)"},
                "fim": {"type": "string", "description": "AAAA-MM-DD (padrão: hoje)"},
                "termo": {"type": "string", "description": "Pedaço da descrição, ex.: 'ifood'"},
                "categoria": {"type": "string"}, "conta": {"type": "string"}, "cartao": {"type": "string"},
                "tipo": {"type": "string", "enum": ["despesa", "receita", "transferencia", "fatura_cartao"]},
            },
        },
    },
    {
        "name": "contas_fixas",
        "description": "Contas que se repetem todo mês (aluguel, luz, assinaturas, salário): criar, atualizar, marcar como paga/recebida no mês, desativar ou reativar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acao": {"type": "string", "enum": ["criar", "atualizar", "pagar", "desativar", "reativar"]},
                "id": {"type": "string", "description": "Código da conta fixa (entre colchetes no retrato)."},
                "descricao": {"type": "string", "description": "Ao criar: o nome. Nas outras ações: identifica a conta, se não tiver o código."},
                "descricao_nova": {"type": "string"},
                "valor": {"type": "number", "description": "Ao pagar: só se o valor deste mês for diferente."},
                "dia": {"type": "integer", "description": "Dia do vencimento (1 a 31)."},
                "tipo": {"type": "string", "enum": ["despesa", "receita"]},
                "categoria": {"type": "string"}, "conta": {"type": "string"}, "cartao": {"type": "string"},
                "data": {"type": "string", "description": "Ao pagar: AAAA-MM-DD se não foi hoje."},
                "mes_referencia": {"type": "string", "description": "Ao pagar: AAAA-MM se for de outro mês (ex.: conta atrasada)."},
            },
            "required": ["acao"],
        },
    },
    {
        "name": "desfazer_ultima_acao",
        "description": "Desfaz a última alteração que você fez (tarefas, lembretes, memória e finanças). Chamar de novo desfaz a anterior.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "configurar_financas",
        "description": "Metas de gasto por categoria e de investimento por mês, e cadastro de contas, cartões e categorias. Também corrige saldo de conta e limite de cartão.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acao": {"type": "string", "enum": ["meta_categoria", "meta_investimento", "criar_conta", "criar_cartao",
                                                     "limite_cartao", "ajustar_saldo", "criar_categoria"]},
                "nome": {"type": "string", "description": "Conta, cartão ou categoria."},
                "categoria": {"type": "string", "description": "Para meta_categoria."},
                "valor": {"type": "number", "description": "Meta em reais por mês (0 remove a meta)."},
                "tipo": {"type": "string", "description": "Conta: corrente, poupança, investimento ou dinheiro. Categoria: despesa ou receita."},
                "banco": {"type": "string"},
                "saldo": {"type": "number", "description": "Saldo atual (criar_conta, ajustar_saldo)."},
                "limite": {"type": "number"},
                "dia_fechamento": {"type": "integer"}, "dia_vencimento": {"type": "integer"},
                "icone": {"type": "string", "description": "Um emoji para a categoria."},
            },
            "required": ["acao"],
        },
    },
]


def _itens(entrada: dict, chave: str) -> list:
    itens = entrada.get(chave)
    if not isinstance(itens, list) or not itens:
        raise ValueError(f"'{chave}' precisa ser uma lista com pelo menos um item")
    return itens


def executar_ferramenta(nome: str, entrada: dict) -> str:
    if nome == "criar_tarefas":
        criadas = []
        for t in _itens(entrada, "tarefas"):
            tarefa_id = repo.criar_tarefa(t)
            criadas.append(f"#{tarefa_id} {t['titulo']}")
        return "criadas: " + "; ".join(criadas)

    if nome == "atualizar_tarefa":
        dados = {k: v for k, v in entrada.items() if k != "id"}
        repo.atualizar_tarefa(int(entrada["id"]), dados)
        return "ok: " + repo.linha_tarefa(repo.obter_tarefa(int(entrada["id"])))

    if nome == "concluir_tarefas":
        resultados = []
        for item in _itens(entrada, "itens"):
            momento = repo.ler_momento(item["quando"]) if item.get("quando") else None
            resultados.append(repo.concluir_tarefa(int(item["id"]), item.get("observacao") or "", momento))
        return "; ".join(resultados)

    if nome == "registrar_feito":
        ids = []
        for item in _itens(entrada, "itens"):
            momento = repo.ler_momento(item["quando"]) if item.get("quando") else None
            tarefa_id = int(item["tarefa_id"]) if item.get("tarefa_id") else None
            ids.append(repo.registrar(item["texto"], item.get("area"), tarefa_id, momento))
        return f"{len(ids)} registro(s) salvo(s)"

    if nome == "buscar_tarefas":
        linhas = repo.buscar_tarefas(entrada.get("termo", ""), entrada.get("status", ""),
                                     entrada.get("criadas_desde"), entrada.get("criadas_ate"))
        if not linhas:
            return "nenhuma tarefa encontrada"
        return "\n".join(
            f"{repo.linha_tarefa(t)} | status: {t.status}"
            + (f" em {t.concluida_em:%d/%m %H:%M}" if t.concluida_em else "")
            for t in linhas
        )

    if nome == "buscar_registros":
        inicio, fim = repo.ler_data(entrada["inicio"]), repo.ler_data(entrada["fim"])
        return repo.formatar_registros(repo.registros_periodo(inicio, fim))

    if nome == "criar_lembrete":
        lembrete_id = repo.criar_lembrete(entrada["quando"], entrada["texto"])
        quando = repo.ler_momento(entrada["quando"])
        aviso = " (atenção: esse horário já passou, vai sair no próximo ciclo)" if quando <= repo.agora() else ""
        return f"lembrete #{lembrete_id} agendado para {quando:%d/%m %H:%M}{aviso}"

    if nome == "cancelar_lembrete":
        return "cancelado" if repo.cancelar_lembrete(int(entrada["id"])) else "lembrete não encontrado ou já enviado"

    if nome == "lembrar_sobre_mim":
        return f"guardado como m{repo.salvar_memoria(entrada['fato'])}"

    if nome == "esquecer_sobre_mim":
        memoria_id = int(str(entrada["id"]).lstrip("mM"))
        return "apagado" if repo.apagar_memoria(memoria_id) else "não encontrado"

    if nome == "lancar_financas":
        return financas.lancar(_itens(entrada, "itens"))

    if nome == "corrigir_lancamento":
        campos = {k: entrada.get(k) for k in ("valor", "data", "descricao", "categoria", "conta", "cartao")}
        return financas.corrigir(entrada["codigo"], bool(entrada.get("remover")),
                                 bool(entrada.get("todas_parcelas")), campos)

    if nome == "consultar_financas":
        return financas.consultar(**{k: entrada.get(k) or "" for k in
                                     ("inicio", "fim", "termo", "categoria", "conta", "cartao", "tipo")})

    if nome == "contas_fixas":
        return financas.contas_fixas(entrada["acao"], {k: v for k, v in entrada.items() if k != "acao"})

    if nome == "desfazer_ultima_acao":
        return diario.desfazer_ultima()

    if nome == "configurar_financas":
        return financas.configurar(entrada["acao"], {k: v for k, v in entrada.items() if k != "acao"})

    raise ValueError(f"ferramenta desconhecida: {nome}")


class SemChave(Exception):
    """Nenhuma chave da Anthropic configurada (nem no app, nem no servidor)."""


def chave() -> str:
    """A chave posta pelo app (Config) vale mais que a variável do servidor."""
    try:
        do_app = repo.estado_get("anthropic_chave")
    except Exception:  # banco fora do ar: segue com a do servidor
        log.warning("Não consegui ler a chave guardada pelo app", exc_info=True)
        do_app = None
    return (do_app or os.getenv("ANTHROPIC_API_KEY") or "").strip()


def testar_chave(valor: str) -> None:
    """Confere com a Anthropic se a chave funciona para a secretária; contar tokens não gasta crédito."""
    anthropic.Anthropic(api_key=valor, timeout=20.0, max_retries=1).messages.count_tokens(
        model=config.MODELO, messages=[{"role": "user", "content": "oi"}])


_cliente = None
_cliente_chave = None


def _cliente_claude() -> anthropic.Anthropic:
    global _cliente, _cliente_chave
    atual = chave()
    if not atual:
        raise SemChave()
    if _cliente is None or atual != _cliente_chave:
        _cliente = anthropic.Anthropic(api_key=atual, timeout=180.0, max_retries=3)
        _cliente_chave = atual
    return _cliente


def _chamar(mensagens: list, modelo: str, esforco: str):
    extras = {}
    if modelo in _SUPORTA_FALLBACK:
        extras = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if not modelo.startswith("claude-haiku"):
        extras["output_config"] = {"effort": esforco}
    return _cliente_claude().beta.messages.create(
        model=modelo,
        max_tokens=16000,
        system=[{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],
        tools=FERRAMENTAS,
        messages=mensagens,
        cache_control={"type": "ephemeral"},
        **extras,
    )


def motivo_falha(erro: Exception) -> str:
    """Diz em português por que a chamada ao Claude falhou, com o motivo que a API devolveu."""
    if isinstance(erro, SemChave):
        return "falta a chave da IA; coloque em Config → Assistente com IA"
    if isinstance(erro, anthropic.AuthenticationError):
        return "a Anthropic recusou a chave da IA; troque em Config → Assistente com IA"
    if isinstance(erro, anthropic.APIStatusError):
        corpo = erro.body if isinstance(erro.body, dict) else {}
        detalhe = str((corpo.get("error") or {}).get("message") or "")
        if "credit balance" in detalhe.lower():
            return "acabou o crédito da Anthropic; dá para colocar mais em console.anthropic.com, em Billing"
        if detalhe:
            return f"{type(erro).__name__}: {detalhe[:300]}"
    return type(erro).__name__


def _texto(resposta) -> str:
    return "\n\n".join(b.text.strip() for b in resposta.content if b.type == "text" and b.text.strip())


def _anotar_uso(modelo: str, resposta) -> None:
    try:
        repo.registrar_uso(getattr(resposta, "model", None) or modelo, resposta.usage)
    except Exception:  # medir custo nunca pode atrapalhar a resposta
        log.warning("Não consegui registrar o uso da API", exc_info=True)


def responder(pedido: str, planejamento: bool = False, max_rodadas: int = 10) -> str:
    """Roda o laço de ferramentas até a secretária ter a resposta final.

    planejamento=True usa o modelo mais capaz (plano do dia, fechamento,
    /adiantar, relatório); o resto vai no modelo barato do dia a dia."""
    modelo = config.MODELO_PLANEJAMENTO if planejamento else config.MODELO
    esforco = config.ESFORCO_PLANEJAMENTO if planejamento else config.ESFORCO_CONVERSA
    mensagens = [{"role": "user", "content": pedido}]
    for _ in range(max_rodadas):
        resposta = _chamar(mensagens, modelo, esforco)
        _anotar_uso(modelo, resposta)
        if resposta.stop_reason == "refusal":
            log.warning("Pedido recusado: %s", getattr(resposta, "stop_details", None))
            return "Não consegui processar isso. Pode me dizer de outro jeito?"
        chamadas = [b for b in resposta.content if b.type == "tool_use"]
        if resposta.stop_reason != "tool_use" or not chamadas:
            texto = _texto(resposta)
            if resposta.stop_reason == "max_tokens" and not texto:
                return "Me perdi pensando nisso. Pode mandar de novo, mais direto?"
            return texto or "Feito."
        mensagens.append({"role": "assistant", "content": resposta.content})
        resultados = []
        for chamada in chamadas:
            try:
                saida, erro = executar_ferramenta(chamada.name, dict(chamada.input or {})), False
            except (ValueError, KeyError, TypeError) as e:
                saida, erro = f"Erro: {e}", True
            log.info("ferramenta %s(%s) -> %s", chamada.name,
                     json.dumps(chamada.input, ensure_ascii=False)[:300], saida[:300])
            resultados.append({"type": "tool_result", "tool_use_id": chamada.id,
                               "content": saida, "is_error": erro})
        mensagens.append({"role": "user", "content": resultados})
    return "Fiz várias coisas aqui e acabei me enrolando. Confere com /tarefas e /financas se ficou certo?"


# Preço por milhão de tokens em US$: entrada, saída, escrita de cache (5 min), leitura de cache.
PRECOS = {
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 5.00, 0.20),
}


def custo_em_dolar(modelo: str, uso: dict) -> float:
    base = next((p for nome, p in PRECOS.items() if modelo.startswith(nome)), None)
    if base is None:
        return 0.0
    entrada, saida, escrita, leitura = base
    return (uso.get("entrada", 0) * entrada + uso.get("saida", 0) * saida
            + uso.get("cache_escrita", 0) * escrita + uso.get("cache_leitura", 0) * leitura) / 1_000_000
