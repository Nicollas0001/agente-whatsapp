"""O cérebro da secretária: prompt, ferramentas e o laço de conversa com o Claude."""
import json
import logging

import anthropic
from sqlalchemy.orm import Session

from app.secretaria import config, repositorio as repo

log = logging.getLogger("secretaria.ia")

# Modelos que aceitam o fallback automático do servidor quando um classificador
# de segurança recusa um pedido por engano (evita a secretária "travar").
_SUPORTA_FALLBACK = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

SISTEMA = f"""Você é a secretária pessoal de uma pessoa ocupada que concilia vida pessoal e profissional. Vocês conversam pelo Telegram. Seu trabalho: guardar tudo o que ela precisa fazer, dizer o que fazer a cada dia, anotar o que ela vai fazendo e ajudar a pensar à frente.

<jeito>
- Fale como uma secretária experiente e de confiança: português do Brasil, natural, frases curtas, tom de conversa. Nada de linguagem corporativa e nada de bajulação ("ótima pergunta", "que incrível"). Elogio só quando houver motivo concreto, e curto.
- Seja honesta. Se o dia está impossível, diga. Se uma tarefa vem sendo empurrada, comente com tato e proponha uma saída: quebrar em partes, marcar hora, delegar ou desistir dela.
- Pense como gente, não como lista: energia (o que exige foco vai cedo, burocracia em horário de baixa energia), deslocamentos, horário de trabalho, compromissos com hora marcada, imprevistos (planeje uns 70% do tempo livre, não 100%), e descanso e vida pessoal também contam.
- Não invente. Nunca crie tarefa, prazo ou fato que a pessoa não disse. Se algo for ambíguo e fizer diferença (qual tarefa? que dia?), pergunte, uma pergunta só e objetiva. Se não fizer diferença, decida o óbvio e diga o que assumiu.
</jeito>

<como_trabalhar>
- Todo pedido vem com um RETRATO atualizado: data e hora, tarefas abertas com #id, o que já foi feito hoje, lembretes e o que você sabe sobre a pessoa. Confie nele. Use buscar_tarefas e buscar_registros só para o que não está ali (tarefas concluídas, histórico antigo).
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


def _itens(entrada: dict, chave: str) -> list:
    itens = entrada.get(chave)
    if not isinstance(itens, list) or not itens:
        raise ValueError(f"'{chave}' precisa ser uma lista com pelo menos um item")
    return itens


def executar_ferramenta(db: Session, nome: str, entrada: dict) -> str:
    if nome == "criar_tarefas":
        criadas = []
        for t in _itens(entrada, "tarefas"):
            tarefa_id = repo.criar_tarefa(db, t)
            criadas.append(f"#{tarefa_id} {t['titulo']}")
        return "criadas: " + "; ".join(criadas)

    if nome == "atualizar_tarefa":
        dados = {k: v for k, v in entrada.items() if k != "id"}
        repo.atualizar_tarefa(db, int(entrada["id"]), dados)
        return "ok: " + repo.linha_tarefa(repo.obter_tarefa(db, int(entrada["id"])))

    if nome == "concluir_tarefas":
        resultados = []
        for item in _itens(entrada, "itens"):
            momento = repo.ler_momento(item["quando"]) if item.get("quando") else None
            resultados.append(repo.concluir_tarefa(db, int(item["id"]), item.get("observacao") or "", momento))
        return "; ".join(resultados)

    if nome == "registrar_feito":
        ids = []
        for item in _itens(entrada, "itens"):
            momento = repo.ler_momento(item["quando"]) if item.get("quando") else None
            tarefa_id = int(item["tarefa_id"]) if item.get("tarefa_id") else None
            ids.append(repo.registrar(db, item["texto"], item.get("area"), tarefa_id, momento))
        return f"{len(ids)} registro(s) salvo(s)"

    if nome == "buscar_tarefas":
        linhas = repo.buscar_tarefas(db, entrada.get("termo", ""), entrada.get("status", ""),
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
        return repo.formatar_registros(repo.registros_periodo(db, inicio, fim))

    if nome == "criar_lembrete":
        lembrete_id = repo.criar_lembrete(db, entrada["quando"], entrada["texto"])
        quando = repo.ler_momento(entrada["quando"])
        aviso = " (atenção: esse horário já passou, vai sair no próximo ciclo)" if quando <= repo.agora() else ""
        return f"lembrete #{lembrete_id} agendado para {quando:%d/%m %H:%M}{aviso}"

    if nome == "cancelar_lembrete":
        return "cancelado" if repo.cancelar_lembrete(db, int(entrada["id"])) else "lembrete não encontrado ou já enviado"

    if nome == "lembrar_sobre_mim":
        return f"guardado como m{repo.salvar_memoria(db, entrada['fato'])}"

    if nome == "esquecer_sobre_mim":
        memoria_id = int(str(entrada["id"]).lstrip("mM"))
        return "apagado" if repo.apagar_memoria(db, memoria_id) else "não encontrado"

    raise ValueError(f"ferramenta desconhecida: {nome}")


_cliente = None


def _cliente_claude() -> anthropic.Anthropic:
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(timeout=180.0, max_retries=3)
    return _cliente


def _chamar(mensagens: list, esforco: str):
    extras = {}
    if config.MODELO in _SUPORTA_FALLBACK:
        extras = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    if not config.MODELO.startswith("claude-haiku"):
        extras["output_config"] = {"effort": esforco}
    return _cliente_claude().beta.messages.create(
        model=config.MODELO,
        max_tokens=16000,
        system=[{"type": "text", "text": SISTEMA, "cache_control": {"type": "ephemeral"}}],
        tools=FERRAMENTAS,
        messages=mensagens,
        cache_control={"type": "ephemeral"},
        **extras,
    )


def _texto(resposta) -> str:
    return "\n\n".join(b.text.strip() for b in resposta.content if b.type == "text" and b.text.strip())


def responder(db: Session, pedido: str, esforco: str = None, max_rodadas: int = 10) -> str:
    """Roda o laço de ferramentas até a secretária ter a resposta final."""
    esforco = esforco or config.ESFORCO_CONVERSA
    mensagens = [{"role": "user", "content": pedido}]
    for _ in range(max_rodadas):
        resposta = _chamar(mensagens, esforco)
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
                saida, erro = executar_ferramenta(db, chamada.name, dict(chamada.input or {})), False
            except (ValueError, KeyError, TypeError) as e:
                db.rollback()
                saida, erro = f"Erro: {e}", True
            log.info("ferramenta %s(%s) -> %s", chamada.name,
                     json.dumps(chamada.input, ensure_ascii=False)[:300], saida[:300])
            resultados.append({"type": "tool_result", "tool_use_id": chamada.id,
                               "content": saida, "is_error": erro})
        mensagens.append({"role": "user", "content": resultados})
    return "Fiz várias coisas aqui e acabei me enrolando. Confere com /tarefas se ficou certo?"
