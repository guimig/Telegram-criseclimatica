# Telegram-criseclimatica

Bot separado para notificar no grupo Telegram **mudanças de nível de risco consultivo de Araquari**, a partir dos JSON públicos do [Observatório do Tempo e Clima](https://guimig.github.io/criseclimatica/).

## Como funciona

O cron-job.org dispara a GitHub Action a cada dez minutos. O agendamento nativo do GitHub é uma contingência: a cada dez minutos, verifica se houve sucesso confirmado de um disparo externo nos últimos 15 minutos; somente quando não houve, ou quando a consulta ao histórico falhar, executa o monitor. São validados identidade do município, natureza consultiva, atualidade (até duas horas), integridade e compatibilidade entre horários de publicação. A fonte de verdade é dashboard.risk_classification; o bot não recalcula risco e não atribui nível oficial.

O histórico identifica episódios amostrados de mudança e pode recuperar alterações entre duas verificações. Uma mensagem informa nível anterior e atual, subida/redução, data da coleta, justificativa e até três evidências quando fornecidas, com link para o painel. Episódios recuperados são marcados como históricos e não são apresentados como risco atual. O estado parcial significa **Sem classificação**, e não normalidade.

A primeira execução apenas registra o estado inicial sem anunciar uma alteração retroativa. Repetições de nível não geram mensagem.

## Ativação

1. Crie um bot com [@BotFather](https://t.me/BotFather), adicione-o ao grupo e conceda permissão de enviar mensagens.
2. Descubra o identificador do grupo (chat_id) com o método getUpdates da Telegram Bot API. Não exponha o token em logs, links ou no repositório.
3. No repositório, em Settings > Secrets and variables > Actions, crie **TELEGRAM_TOKEN** e **TELEGRAM_CHAT_ID**.
4. A branch **bot-state** contém o state.json persistente. Em Settings > Actions > General, permita gravação de conteúdo pelo GITHUB_TOKEN (Read and write permissions), considerando eventuais regras de proteção da branch.
5. Faça merge da PR e abra Actions > Monitorar risco consultivo - Telegram. Execute primeiro no padrão dry_run=true (simulação). Para testar uma mensagem real, marque test_message=true e dry_run=false; a mensagem é explicitamente rotulada TESTE. Por fim execute com as duas opções falsas para inicializar o cursor.
6. Configure o cron-job.org conforme a seção seguinte. Enquanto o disparador externo não estiver ativo, o agendamento de contingência no GitHub continuará realizando a verificação real. A pontualidade do agendamento não é garantida.

## Agendamento principal pelo cron-job.org e backup no GitHub

**Modelo:** o cron-job.org realiza o disparo às horas UTC com minutos 00, 10, 20, 30, 40 e 50. O GitHub agenda verificações nos minutos 07, 17, 27, 37, 47 e 57, mas **não executa o monitor** quando uma execução externa recente e bem-sucedida já ocorreu. A janela de tolerância de 15 minutos permite recuperação após uma falha do disparador principal. Atrasos reais de cron-job.org e do agendador GitHub ainda são possíveis.

### Configuração do cron-job.org

Acesse sua própria conta em https://cron-job.org e crie um cronjob com os seguintes campos:

| Campo | Valor |
| --- | --- |
| Nome | `Telegram-criseclimatica - monitor principal` |
| URL | `https://api.github.com/repos/guimig/Telegram-criseclimatica/actions/workflows/monitor.yml/dispatches` |
| Método | `POST` |
| Frequência | A cada 10 minutos, nos minutos 00, 10, 20, 30, 40 e 50 |
| Cabeçalho `Accept` | `application/vnd.github+json` |
| Cabeçalho `Content-Type` | `application/json` |
| Cabeçalho `Authorization` | `Bearer SEU_TOKEN_GITHUB` |
| Cabeçalho `X-GitHub-Api-Version` | `2022-11-28` |

O corpo da requisição deve ser este JSON:

```json
{
  "ref": "main",
  "inputs": {
    "source": "cron-job.org",
    "dry_run": "false",
    "test_message": "false"
  }
}
```

**Token:** crie em GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens. Escolha **somente** o repositório `guimig/Telegram-criseclimatica` e a permissão **Actions: Read and write**. Prefira validade limitada e configure renovação antes do vencimento. Cole o token apenas no cabeçalho privado do cronjob; **não** o coloque no URL, no corpo do JSON, no repositório ou nesta conversa. O token de Telegram permanece somente nos GitHub Secrets já configurados. O token do GitHub deve estar no cron-job.org e não precisa ser criado como novo segredo do GitHub.

Ao utilizar **Executar agora** no cron-job.org, a API do GitHub deve retornar um HTTP de sucesso e gerar uma execução na aba Actions intitulada **Telegram risco | cron-job.org**. A resposta do disparo indica aceite da requisição, **não** garante envio de mensagem ao grupo. O grupo recebe notificações somente quando o nível de risco muda.

### Como funciona a contingência

A rotina `scripts/check_backup.py` usa o `GITHUB_TOKEN` já fornecido pelo GitHub Actions para consultar a API de execuções do próprio workflow. Ela somente considera execuções completas, bem-sucedidas, na `main`, com identificação **Telegram risco | cron-job.org**. Disparos manuais, simulados, falhos ou antigos não desativam a contingência.

- Caso exista sucesso externo há **até 15 minutos**, o GitHub registra `should_run=false` e **pula** testes, leitura de estado, monitor e persistência.
- Se não houver sucesso externo recente, executa o monitor com os mesmos segredos e o mesmo cursor persistido da branch `bot-state`.
- Se a API de verificação falhar, a contingência executa por segurança. Isso pode causar execução redundante, mas o controle de transições evita notificações de estado repetidas.
- A serialização por `concurrency` evita que dois disparos do mesmo workflow trabalhem em paralelo. Uma falha entre envio confirmado ao Telegram e gravação do cursor ainda pode gerar duplicação, uma limitação preexistente.

O agendamento de contingência **não equivale a um serviço permanentemente em execução**: ele verifica a saúde e somente efetua a consulta meteorológica se necessário. Não desligue o workflow do GitHub, pois ele concentra tanto os disparos externos quanto o backup.

Para testar o backup sem mudar o risco real, use os testes automatizados em `tests/test_backup.py`. Para verificar o comportamento de produção, observe os logs da etapa **Avaliar disparador principal (somente contingencia)** depois de configurar o cron-job.org. O arquivo `state.json` não é usado como indicador de atividade, pois só registra mudanças de classificação.

## Segurança, persistência e limites

- Token e chat ID ficam em GitHub Actions Secrets. Nenhuma chave deve ser inserida no código ou nos arquivos públicos.
- O cursor é armazenado exclusivamente na branch bot-state, isolado da main. O workflow só faz commit de estado quando há progresso.
- Envio só é considerado confirmado após resposta positiva do Telegram. Uma falha entre a confirmação e o commit pode causar duplicação em vez de perda silenciosa.
- Dados atrasados, versões misturadas ou perda do episódio de referência interrompem o monitor e exigem avaliação, sem reinicialização automática.
- A memória do Observatório contém apenas amostras de estados publicados; transições não publicadas são impossíveis de recuperar.
- O bot **não substitui avisos da Defesa Civil, não emite decisões institucionais e não determina suspensão de aulas**.
- Para pausar notificações, desative o workflow pelo GitHub Actions.

## Como confirmar um envio real ao grupo

**Atenção:** o workflow **Testes do robo** apenas executa testes locais com envio simulado. Aprovação do CI não significa mensagem entregue. A versão anterior desses testes imprimia indevidamente `[ENVIADO] ... message_id=42`: isso era **fictício**.

Para testar o grupo de fato:

1. Acesse **Actions → Monitorar risco consultivo - Telegram → Run workflow** na branch `main`.
2. Defina **dry_run = false** e **test_message = true**. A combinação `dry_run = true` (padrão) **não envia mensagens**.
3. Abra a execução de **Monitorar risco consultivo - Telegram**, etapa **Monitorar publicacao**, e procure `[TESTE] message_id=`. Esse registro, após a chamada da API, indica que o Telegram confirmou envio ao grupo configurado.
4. Se ocorrer erro, confira os secrets `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID`, o ID numérico **negativo** do grupo e a participação/permissão do bot. Nunca divulgue o token. Caso use tópicos, verifique o tópico geral.

O cliente agora confere no retorno da API o `chat.type` (grupo ou supergrupo) e o `chat.id` de destino, sem publicar o identificador nos logs. A primeira execução normal (`dry_run = false`, `test_message = false`) apenas inicializa o cursor sem notificar mudança retroativa.

## Testes

Python 3.11+; apenas biblioteca padrão. Execute:

- python -m unittest discover -s tests -v
- python -m compileall -q src tests
- python -m src.main --dry-run --state state.json
- python -m src.main --test-message

O arquivo state.json local precisa existir e ter schema_version=1, location_id=araquari, last_episode_at=null e last_level=null. Não precisa de credenciais para dry-run. O envio real só pode ser verificado após a configuração do grupo.
