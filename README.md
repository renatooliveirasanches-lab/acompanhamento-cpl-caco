# Acompanhamento CPL — Caco Amigurumi

Robô que roda na nuvem do GitHub **de hora em hora** e acompanha os CPLs no YouTube:
lê as views/likes/comentários públicos, calcula o **% de views sobre os leads** e
compara o CPL atual com os lançamentos anteriores no **mesmo ponto do tempo**
(mesma hora desde a publicação). Avisa no **Telegram**.

## Como funciona
- **Configuração:** a planilha do Google (aba `CONFIG`). É só lá que o Renato mexe.
- **Motor:** GitHub Actions (`.github/workflows/coletar.yml`), de hora em hora.
- **Histórico:** `dados/snapshots.csv` (o robô salva sozinho a cada coleta).
- **Avisos:** grupo do Telegram (a equipe toda acompanha).

Não usa login do Google — lê o YouTube por uma **chave de API**, então não esbarra
em trava de segurança nenhuma.

## Segredos (GitHub → Settings → Secrets and variables → Actions)
- `YOUTUBE_API_KEY` — chave da YouTube Data API
- `TELEGRAM_TOKEN` — token do bot @VENDASCACOBOT
- `TELEGRAM_CHAT_ID` — id do grupo DASHBOARD CACO AMIGURUMI

## No dia a dia
Na aba `CONFIG` da planilha, adicione as linhas dos CPLs (LANCAMENTO, TAG,
PERSONAGEM, CPL, VIDEO_ID, LEADS, ATIVO=sim). Quando o lançamento acabar,
troque `ATIVO` para `não`. O resto é automático.
