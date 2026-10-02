# agente-whatsapp

API FastAPI com o bot de WhatsApp (Z-API, em `/zap`) e a **secretária pessoal no Telegram** (em `/secretaria`), que também cuida das finanças junto com o app `financas.html`.

## Secretária pessoal

Você fala com ela no Telegram, em português normal, e ela:

- guarda suas tarefas pessoais e profissionais, manda o plano do dia e faz o fechamento à noite;
- anota o que você foi fazendo e monta relatório quando você pede;
- aponta o que dá pra fazer agora para adiantar o futuro (`/adiantar`);
- cuida do dinheiro: gastos, entradas, saques, transferências, fatura do cartão, compras parceladas, contas do mês e assinaturas, aportes e resgates, metas por categoria e de investimento.

**Tudo fica num lugar só:** o Firebase que o `financas.html` já usa. O que você lança no Telegram aparece no app na hora, e o que você lança no app ela enxerga.

### O que ela faz sozinha

| Quando | O quê |
|---|---|
| 06:00 | Plano do dia: até 3 prioridades, compromissos com hora, coisas rápidas, **💰 Dinheiro** (conta vencendo, fatura, meta estourando) e **💡 Adiantar** |
| 20:00 | Fechamento: o que foi feito, o que ficou, quanto gastou hoje, se esqueceu de anotar algum gasto |
| Hora marcada | Lembretes que você pediu ("me lembra às 15h de...") |
| Virada do dia | O que ficou para trás vem pra hoje e conta como adiamento. A partir de 3 adiamentos, ela comenta |
| Ao lançar gasto | Avisa se a categoria passou de 80% ou 100% da meta, se o limite do cartão está acabando ou se a conta ficou negativa |

Ela só começa a mandar mensagens depois do seu primeiro `/start`. Se você mandar várias mensagens seguidas, ela espera uns segundos e responde tudo de uma vez.

### Como falar com ela

- "Amanhã preciso pagar o IPVA e mandar a proposta pro cliente até sexta"
- "Gastei 45 no iFood no Nubank" / "Comprei um fone de 600 em 6x no Inter Black"
- "Caiu o salário, 3.200 no Inter" / "Saquei 100" / "Paguei a fatura do Nubank pelo Inter"
- "Investi 500 no CDB" / "Resgatei 300"
- "Aluguel de 800 todo dia 5 no Bradesco" (vira conta fixa) e, depois, "paguei o aluguel"
- "Quero gastar no máximo 600 com mercado por mês" (meta da categoria)
- "Quanto gastei com iFood esse mês?" / "Posso comprar um tênis de 400?"

| Atalho | O que faz |
|---|---|
| `/plano` | Monta ou refaz o plano de hoje |
| `/adiantar` | Até 3 coisas pra fazer agora que adiantam o futuro (tarefas e dinheiro) |
| `/relatorio` | O que você fez e gastou hoje. Também `/relatorio ontem`, `semana`, `mes` ou `15` (últimos 15 dias) |
| `/tarefas` | Lista rápida de tudo que está aberto (sem IA) |
| `/financas` | Saldos, faturas, contas do mês e investimentos (sem IA) |
| `/custo` | Quanto a IA custou no mês e a projeção até o fim do mês |
| `/fechamento` | Fecha o dia agora |
| `/ajuda` | Exemplos |

### Quanto custa por mês

| Item | Custo |
|---|---|
| Render (grátis) + cron-job.org | R$0 |
| Firebase, plano Spark (Firestore + login Google) | R$0 |
| Telegram | R$0 |
| IA: Claude Haiku no dia a dia + Claude Sonnet no plano, fechamento, `/adiantar` e relatório | ~US$8–10 (≈R$45–55 + IOF) |

A estimativa supõe umas 10 a 15 mensagens por dia. O `/custo` mostra o valor real. Trave um limite de gasto mensal no console da Anthropic (Settings → Limits) para nunca ter surpresa.

### Configuração (uma vez)

1. **Bot do Telegram:** fale com o [@BotFather](https://t.me/BotFather), mande `/newbot` e guarde o token.
2. **Firebase** ([console](https://console.firebase.google.com), projeto `financas-pessoal-1ca66`):
   1. *Authentication → Sign-in method*: ative o **Google**.
   2. *Authentication → Settings → Authorized domains*: adicione o domínio onde você abre o app (ex.: `nicollas0001.github.io`).
   3. *Configurações do projeto → Contas de serviço → Gerar nova chave privada*: baixa um arquivo JSON. Ele dá acesso total ao banco; não coloque no repositório.
3. **Chave do Claude:** em [console.anthropic.com](https://console.anthropic.com), crie uma API key, coloque créditos e defina o limite mensal.
4. **Render**, nas *Environment Variables* do serviço:

   | Variável | Valor |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | token do passo 1 |
   | `FIREBASE_CREDENCIAIS` | o conteúdo inteiro do JSON do passo 2.3 (ou ele em base64) |
   | `ANTHROPIC_API_KEY` | chave do passo 3 |
   | `CRON_SECRET` | uma senha qualquer, inventada por você |

   Faça o deploy. O webhook do Telegram se configura sozinho (fora do Render, defina `PUBLIC_URL`).
5. **Seu ID:** mande `/start` pro bot. Ele responde com o seu ID. Coloque em `TELEGRAM_OWNER_ID`, faça o deploy de novo e mande `/start` outra vez. Daí em diante, ela só responde a você.
6. **Despertador do servidor:** o Render grátis desliga depois de 15 min sem acesso. Em [cron-job.org](https://cron-job.org), crie um job a cada 10 minutos chamando
   `https://SEU-APP.onrender.com/secretaria/tick?chave=SEU_CRON_SECRET`.
   Isso mantém o servidor acordado e dispara plano, fechamento e lembretes. Pode chamar quantas vezes quiser: nada sai duplicado.
7. **App:** abra o `financas.html` atualizado em cada aparelho e toque em **Entrar com Google** (ou na 🔑 do menu).
8. **Só depois do passo 7**, feche o banco: *Firestore → Regras*, cole o conteúdo de [`firestore.rules`](firestore.rules) trocando `SEU_EMAIL@gmail.com` pelo seu e-mail e publique. Antes disso, qualquer pessoa com o endereço do projeto consegue ler e apagar seus dados financeiros. Se publicar antes de entrar no app, o app para de sincronizar até você fazer login.
9. **Conferir:** `https://SEU-APP.onrender.com/secretaria/status` deve mostrar tudo como `true`.

### Ajustes opcionais

| Variável | Padrão | Para quê |
|---|---|---|
| `SECRETARIA_HORA_PLANO` | `06:00` | Horário do plano |
| `SECRETARIA_HORA_FECHAMENTO` | `20:00` | Horário do fechamento |
| `SECRETARIA_MODELO` | `claude-haiku-4-5` | Modelo do dia a dia |
| `SECRETARIA_MODELO_PLANEJAMENTO` | `claude-sonnet-5-5` | Modelo do plano, fechamento, `/adiantar` e relatório |
| `SECRETARIA_ESFORCO_PLANEJAMENTO` | `medium` | `high` planeja melhor e custa mais |
| `SECRETARIA_COTACAO_DOLAR` | `5.5` | Só para o `/custo` mostrar em reais |
| `SECRETARIA_ESPERA_AGRUPAR_SEG` | `4` | Segundos de espera para juntar mensagens seguidas |
| `SECRETARIA_FUSO` | `America/Sao_Paulo` | Fuso horário |

### Como funciona por dentro

- `app/secretaria/financas.py`: lê e grava o documento `financas/dados`, o mesmo do app, com os mesmos cálculos de saldo, fatura e resumo do mês. Cada gravação é uma transação que aumenta o número de revisão (`rev`).
- `financas.html`: só sincroniza com login Google, recebe as mudanças em tempo real e, ao salvar, junta as mudanças dos dois lados por id em vez de sobrescrever o documento. Passaram a sincronizar também as metas por categoria, os objetivos de compra e as configurações de investimento, que antes ficavam só no aparelho.
- `app/secretaria/repositorio.py`: tarefas, registros, lembretes, memória e mensagens no Firestore (coleções `sec_*`, que só o servidor acessa).
- `app/secretaria/ia.py`: prompt, as 15 ferramentas (10 de tarefas e 5 de finanças) e o laço com o Claude, com a troca de modelo por tipo de pedido e a medição de custo.
- `app/secretaria/rotinas.py`: plano, fechamento, lembretes, comandos e o agrupamento de mensagens.

### Testes

```bash
pip install -r requirements.txt pytest httpx playwright
firebase emulators:start --only firestore,auth --project demo-secretaria   # em outro terminal
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 python -m pytest tests
node --test tests/sync_merge.test.mjs
```

O `tests/test_app_e2e.py` abre o `financas.html` num navegador de verdade, contra os emuladores com as regras de segurança. Veja no topo do arquivo o que ele precisa.

**Limitações atuais:** só entende texto (áudio e foto de cupom ainda não), atende uma pessoa só, e o app guarda todos os lançamentos num único documento do Firestore, que tem limite de 1 MB (alguns milhares de lançamentos; um dia vai precisar dividir).
