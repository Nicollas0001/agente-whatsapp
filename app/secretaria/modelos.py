from sqlalchemy import Table, Column, Integer, String, Text, Date, DateTime, Boolean
from app.models.database import metadata

# Datas e horas são guardadas no horário local (config.FUSO), sem fuso.

tarefas = Table(
    "sec_tarefas", metadata,
    Column("id", Integer, primary_key=True),
    Column("titulo", String(300), nullable=False),
    Column("detalhes", Text),
    Column("area", String(20), nullable=False, default="pessoal"),  # pessoal | profissional
    Column("status", String(20), nullable=False, default="pendente"),  # pendente | aguardando | feita | cancelada
    Column("prazo", Date),  # até quando precisa estar pronta
    Column("agendada_para", Date),  # dia em que pretende fazer
    Column("hora", String(5)),  # compromisso com hora marcada, "14:30"
    Column("prioridade", Integer, nullable=False, default=2),  # 1 alta, 2 média, 3 baixa
    Column("esforco_min", Integer),
    Column("contexto", String(200)),  # onde/com o quê: "rua", "computador", "telefone"...
    Column("depende_de", String(200)),  # ids separados por vírgula
    Column("recorrencia", String(60)),  # ver recorrencia.proxima_data
    Column("adiamentos", Integer, nullable=False, default=0),
    Column("criada_em", DateTime, nullable=False),
    Column("concluida_em", DateTime),
)

registros = Table(
    "sec_registros", metadata,
    Column("id", Integer, primary_key=True),
    Column("momento", DateTime, nullable=False),
    Column("texto", Text, nullable=False),
    Column("area", String(20)),
    Column("tarefa_id", Integer),
)

lembretes = Table(
    "sec_lembretes", metadata,
    Column("id", Integer, primary_key=True),
    Column("quando", DateTime, nullable=False),
    Column("texto", Text, nullable=False),
    Column("enviado", Boolean, nullable=False, default=False),
    Column("criado_em", DateTime, nullable=False),
)

memorias = Table(
    "sec_memorias", metadata,
    Column("id", Integer, primary_key=True),
    Column("fato", Text, nullable=False),
    Column("criada_em", DateTime, nullable=False),
)

mensagens = Table(
    "sec_mensagens", metadata,
    Column("id", Integer, primary_key=True),
    Column("papel", String(10), nullable=False),  # user | assistant
    Column("texto", Text, nullable=False),
    Column("momento", DateTime, nullable=False),
    Column("processada", Boolean, nullable=False, default=True),
)

estado = Table(
    "sec_estado", metadata,
    Column("chave", String(100), primary_key=True),
    Column("valor", Text),
)
