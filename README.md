# Telegram-criseclimatica

Bot separado para notificar no grupo Telegram **mudanças de nível de risco consultivo de Araquari**, a partir dos JSON públicos do [Observatório do Tempo e Clima](https://guimig.github.io/criseclimatica/).

## Como funciona

A GitHub Action consulta a cada dez minutos o dashboard.json e o assessment_history.json públicos. São validados identidade do município, natureza consultiva, atualidade (até duas horas), integridade e compatibilidade entre horários de publicação. A fonte de verdade é dashboard.risk_classification; o bot não recalcula risco e não atribui nível oficial.

O histórico identifica episódios amostrados de mudança e pode recuperar alterações entre duas verificações. Uma mensagem informa nível anterior e atual, subida/redução, data da coleta, justificativa e até três evidências quando fornecidas, com link para o painel. Episódios recuperados são marcados como históricos e não são apresentados como risco atual. O estado parcial significa **Sem classificação**, e não normalidade.

A primeira execução apenas registra o estado inicial sem anunciar uma alteração retroativa. Repetições de nível não geram mensagem.

## Ativação

1. Crie um bot com [@BotFather](https://t.me/BotFather), adicione-o ao grupo e conceda permissão de enviar mensagens.
2. Descubra o identificador do grupo (chat_id) com o método getUpdates da Telegram Bot API. Não exponha o token em logs, links ou no repositório.
3. No repositório, em Settings > Secrets and variables > Actions, crie **TELEGRAM_TOKEN** e **TELEGRAM_CHAT_ID**.
4. A branch **bot-state** contém o state.json persistente. Em Settings > Actions > General, permita gravação de conteúdo pelo GITHUB_TOKEN (Read and write permissions), considerando eventuais regras de proteção da branch.
5. Faça merge da PR e abra Actions > Monitorar risco consultivo - Telegram. Execute primeiro no padrão dry_run=true (simulação). Para testar uma mensagem real, marque test_message=true e dry_run=false; a mensagem é explicitamente rotulada TESTE. Por fim execute com as duas opções falsas para inicializar o cursor.
6. O monitor programado roda a cada dez minutos, podendo sofrer atrasos normais do GitHub Actions; não há garantia de disparo exato.

## Segurança, persistência e limites

- Token e chat ID ficam em GitHub Actions Secrets. Nenhuma chave deve ser inserida no código ou nos arquivos públicos.
- O cursor é armazenado exclusivamente na branch bot-state, isolado da main. O workflow só faz commit de estado quando há progresso.
- Envio só é considerado confirmado após resposta positiva do Telegram. Uma falha entre a confirmação e o commit pode causar duplicação em vez de perda silenciosa.
- Dados atrasados, versões misturadas ou perda do episódio de referência interrompem o monitor e exigem avaliação, sem reinicialização automática.
- A memória do Observatório contém apenas amostras de estados publicados; transições não publicadas são impossíveis de recuperar.
- O bot **não substitui avisos da Defesa Civil, não emite decisões institucionais e não determina suspensão de aulas**.
- Para pausar notificações, desative o workflow pelo GitHub Actions.

## Testes

Python 3.11+; apenas biblioteca padrão. Execute:

- python -m unittest discover -s tests -v
- python -m compileall -q src tests
- python -m src.main --dry-run --state state.json
- python -m src.main --test-message

O arquivo state.json local precisa existir e ter schema_version=1, location_id=araquari, last_episode_at=null e last_level=null. Não precisa de credenciais para dry-run. O envio real só pode ser verificado após a configuração do grupo.
