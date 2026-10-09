# agente-whatsapp

API FastAPI com o bot de WhatsApp (Z-API, em `/zap`) e a **secretária pessoal**: um app instalado no tablet que junta tarefas e finanças, com uma IA que conversa com você e mexe em tudo.

## Secretária pessoal

O app é o `financas.html`, servido pelo próprio servidor em `/app/` e instalado na tela inicial do tablet (Android). Nele você tem:

- **💬 Secretária:** conversa em português normal. Ela anota tarefas, lança gastos e entradas, conclui, reagenda, corrige e apaga, sem pedir confirmação, e avisa o que fez. Toda alteração dela pode ser desfeita no botão **↩️ Desfazer** (ou dizendo "desfaz").
- **✅ Tarefas:** lista do dia, próximos dias, sem data e aguardando outras pessoas. Dá para concluir, editar e criar na mão.
- **As telas de sempre do app** (contas, cartões, lançamentos, investimentos, relatórios), agora com os mesmos dados que a secretária usa, em tempo real.
- **Notificações no tablet:** plano do dia, fechamento e lembretes chegam como notificação do app.

### O que ela faz sozinha

| Quando | O quê |
|---|---|
| 06:00 | Plano do dia: até 3 prioridades, compromissos com hora, coisas rápidas, **💰 Dinheiro** (conta vencendo, fatura, meta estourando) e **💡 Adiantar** |
| 20:00 | Fechamento: o que foi feito, o que ficou, quanto gastou hoje, se esqueceu de anotar algum gasto |
| Hora marcada | Lembretes que você pediu ("me lembra às 15h de...") |
| Virada do dia | O que ficou para trás vem pra hoje e conta como adiamento. A partir de 3 adiamentos, ela comenta |
| Ao lançar gasto | Avisa se a categoria passou de 80% ou 100% da meta, se o limite do cartão está acabando ou se a conta ficou negativa |

Ela só começa a mandar mensagens por conta própria depois que você fala com ela pela primeira vez ou liga as notificações.

### Como falar com ela

- "Amanhã preciso pagar o IPVA e mandar a proposta pro cliente até sexta"
- "Gastei 45 no iFood no Nubank" / "Comprei um fone de 600 em 6x no Inter Black"
- "Caiu o salário, 3.200 no Inter" / "Saquei 100" / "Paguei a fatura do Nubank pelo Inter"
- "Investi 500 no CDB" / "Transferi 500 do Inter pro BTG" (conta de investimento cadastrada)
- "Aluguel de 800 todo dia 5 no Bradesco" (vira conta fixa) e, depois, "paguei o aluguel"
- "Quero gastar no máximo 600 com mercado por mês" (meta da categoria)
- "Quanto gastei com iFood esse mês?" / "Posso comprar um tênis de 400?"
- "Apaga a tarefa do banheiro" / "O iFood foi 52, não 45" / "Desfaz"

Os botões no topo da conversa fazem o mesmo que os atalhos: **Plano**, **Adiantar**, **Finanças**, **Tarefas**, **Relatório** (aceita `/relatorio semana`, `mes`, `ontem` ou `15`), **Fechar o dia** e **Custo**.

### Quanto custa por mês

| Item | Custo |
|---|---|
| Render (grátis) + despertador no GitHub Actions | R$0 |
| Firebase, plano Spark (Firestore + login Google) | R$0 |
| Notificações no tablet (Web Push do Chrome) | R$0 |
| IA: Claude Haiku no dia a dia + Claude Sonnet no plano, fechamento, Adiantar e Relatório | ~US$8–10 (≈R$45–55 + IOF) |

A estimativa supõe umas 10 a 15 mensagens por dia. O botão **Custo** mostra o valor real do mês. Trave um limite mensal no console da Anthropic (Settings → Limits) para nunca ter surpresa.

### Configuração (uma vez)

1. **Firebase** ([console](https://console.firebase.google.com), projeto `financas-pessoal-1ca66`):
   1. *Authentication → Método de login*: ative o **Google**.
   2. *Configurações do projeto → Contas de serviço → Gerar nova chave privada*: baixa um arquivo JSON. Ele dá acesso total ao banco; não coloque no repositório nem mande para ninguém.
2. **Chave do Claude:** em [platform.claude.com](https://platform.claude.com), coloque créditos (com a recarga automática desligada) e crie uma chave em *Chaves de API → Criar chave* com **Escopo: Espaço de trabalho padrão** e **Expira: Nunca**. Chave com escopo "Organização" é recusada.
3. **Render:** abra [render.com/deploy?repo=https://github.com/Nicollas0001/agente-whatsapp](https://render.com/deploy?repo=https://github.com/Nicollas0001/agente-whatsapp), entre com o GitHub e confirme. O [`render.yaml`](render.yaml) cria o servidor sozinho e só pede três valores:

   | Variável | Valor |
   |---|---|
   | `FIREBASE_CREDENCIAIS` | o conteúdo inteiro do JSON do passo 1.2 |
   | `ANTHROPIC_API_KEY` | chave do passo 2 (ou deixe vazio e cole a chave depois no app, em *Config → Assistente com IA*) |
   | `DONO_EMAIL` | o seu e-mail do Google. Só essa conta usa o app |

4. **Domínio:** no Firebase, *Authentication → Configurações → Domínios autorizados*, adicione o endereço que o Render deu (ex.: `secretaria-xxxx.onrender.com`).
5. **Servidor acordado:** o Render grátis põe o servidor para dormir depois de 15 minutos sem visita e, dormindo, mostra uma tela de "acordando" no lugar do app (no Chrome do Android, ela ainda oferece "baixar download.html"). Por isso o próprio servidor visita o seu endereço a cada 10 minutos (`SECRETARIA_MANTER_ACORDADO_MIN`; `0` desliga). Ligado o mês todo, usa umas 744 das 750 horas grátis do Render por mês, então não crie outro serviço grátis na mesma conta. O agendamento do GitHub em [`.github/workflows/secretaria.yml`](.github/workflows/secretaria.yml) fica de reserva: ele atrasa horas e não segura o servidor acordado sozinho. O `/secretaria/status` mostra `no_ar_desde`; se esse horário muda sem deploy, o servidor dormiu.
6. **Instalar no tablet:** abra `https://SEU-APP.onrender.com/app/` no Chrome, entre com Google, toque no menu ⋮ e em **Instalar app** (ou **Adicionar à tela inicial**). Depois, na tela **Secretária**, toque em **🔔 Ligar notificações** e aceite. Deve chegar uma notificação de teste.
7. **Só depois do passo 6**, feche o banco: *Firestore → Regras*, cole o conteúdo de [`firestore.rules`](firestore.rules) trocando `SEU_EMAIL@gmail.com` pelo seu e-mail e publique. Antes disso, qualquer pessoa com o endereço do projeto consegue ler e apagar seus dados financeiros.
8. **Trocar a chave do Claude depois:** no próprio app, *Config → Assistente com IA → Salvar*. O servidor confere com a Anthropic antes de aceitar e a chave fica guardada no Firestore, numa coleção que só o servidor lê. Ela vale mais que a `ANTHROPIC_API_KEY` do Render.
9. **Conferir:** `https://SEU-APP.onrender.com/secretaria/status` deve mostrar `firebase`, `anthropic_key` e `dono_email` como `true`.

O `financas.html` aberto em outro endereço (GitHub Pages, por exemplo) continua funcionando para as finanças, depois que esse domínio também for autorizado no passo 4. A Secretária e as Tarefas só aparecem no endereço do servidor.

### Ajustes opcionais

| Variável | Padrão | Para quê |
|---|---|---|
| `SECRETARIA_HORA_PLANO` | `06:00` | Horário do plano |
| `SECRETARIA_HORA_FECHAMENTO` | `20:00` | Horário do fechamento |
| `SECRETARIA_MODELO` | `claude-haiku-4-5` | Modelo do dia a dia |
| `SECRETARIA_MODELO_PLANEJAMENTO` | `claude-sonnet-5-5` | Modelo do plano, fechamento, Adiantar e Relatório |
| `SECRETARIA_ESFORCO_PLANEJAMENTO` | `medium` | `high` planeja melhor e custa mais |
| `SECRETARIA_COTACAO_DOLAR` | `5.5` | Só para o botão Custo mostrar em reais |
| `SECRETARIA_FUSO` | `America/Sao_Paulo` | Fuso horário |
| `TELEGRAM_BOT_TOKEN` e `TELEGRAM_OWNER_ID` | vazio | Opcional: também conversar e receber os avisos pelo Telegram |

### Como funciona por dentro

- `app/secretaria/rotas_app.py`: API do app (só aceita o token do login Google do `DONO_EMAIL`) e os arquivos do PWA em `/app/` (`pwa/`: manifesto, service worker e ícones).
- `app/secretaria/diario.py`: em cada rodada da IA, guarda como as coisas estavam antes. O desfazer devolve só o que ela mexeu; itens das finanças voltam um a um, pelo id, sem apagar o que você lançou depois no app.
- `app/secretaria/push.py` e `canal.py`: notificações Web Push (as chaves VAPID são geradas sozinhas e guardadas no Firestore) e a entrega das mensagens (histórico do app, notificação e, se ligado, Telegram).
- `app/secretaria/financas.py`: lê e grava o documento `financas/dados` com os mesmos cálculos do app; cada gravação é uma transação com número de revisão (`rev`).
- `financas.html`: login Google, sync em tempo real que junta as mudanças por id em vez de sobrescrever, e as telas Secretária e Tarefas.
- `app/secretaria/ia.py`, `rotinas.py`, `repositorio.py`: prompt e as 16 ferramentas da IA, rotinas automáticas, tarefas e histórico no Firestore (coleções `sec_*`, que só o servidor acessa).

### Testes

```bash
pip install -r requirements.txt pytest httpx playwright
firebase emulators:start --only firestore,auth --project demo-secretaria   # em outro terminal
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python -m pytest tests
node --test tests/sync_merge.test.mjs
```

O `tests/test_app_e2e.py` abre o app num navegador de verdade, contra os emuladores com as regras de segurança: login, conversa, desfazer, tarefas e o app e a secretária gravando ao mesmo tempo. Veja no topo do arquivo o que ele precisa.

**Limitações atuais:** só entende texto (áudio e foto de cupom ainda não), atende uma pessoa só, e o app guarda todos os lançamentos num único documento do Firestore, que tem limite de 1 MB (alguns milhares de lançamentos; um dia vai precisar dividir).
