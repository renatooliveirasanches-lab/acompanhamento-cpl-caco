"""
ACOMPANHAMENTO CPL - CACO AMIGURUMI (versao GitHub)
Roda na nuvem do GitHub de hora em hora. Para cada CPL marcado como ATIVO
na planilha do Google, le as views/likes/comentarios publicos do YouTube
(pela chave de API), calcula o % de views sobre os leads e compara com os
lancamentos anteriores no MESMO ponto do tempo (mesma hora desde a publicacao).
Guarda o historico em dados/snapshots.csv e avisa no Telegram.

Nada de login do Google -> nenhuma trava de seguranca.
O Renato so mexe na planilha (aba CONFIG). O resto e automatico.
"""

import csv
import html
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _get(url, params=None, timeout=30):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _post(url, dados, timeout=30):
    corpo = urllib.parse.urlencode(dados).encode()
    with urllib.request.urlopen(url, corpo, timeout=timeout) as r:
        return r.read().decode("utf-8")

# ===================== CONFIGURACAO =====================

# ID da planilha do Google (aba CONFIG). Nao e segredo.
SHEET_ID = "1ad449AEPFVZ4XJVNp1EugIUFI6E1TngC_BETVRZ2DpI"
ABA_CONFIG = "CONFIG"

# Segredos vem das variaveis de ambiente (os "secrets" do GitHub).
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# usuario-robo do Google (robo-leads-cpl@acompanhamento-cpl.iam.gserviceaccount.com), so leitura
GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", "")

# Inscritos FINAIS de lancamento com a captacao fechada: valem mais que a contagem da planilha da agencia
# (que segue mexendo depois de fechar) e que a coluna LEADS. Nuvem e robo do Mac usam a mesma tabela.
# ponytail: numero fixo por lancamento; no proximo, ou poe aqui ou preenche LEADS na CONFIG.
LEADS_FECHADOS = {"LC0526": 63271}

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_SNAPSHOTS = os.path.join("dados", "snapshots.csv")

CABECALHO_SNAPSHOTS = [
    "TIMESTAMP", "LANCAMENTO", "TAG", "CPL", "VIDEO_ID",
    "HORA_DESDE_PUBLICACAO", "VIEWS", "LIKES", "COMENTARIOS", "PCT_VIEWS",
    "ESPECTADORES_UNICOS", "PCT_UNICOS",  # unicos vem do Studio pelo robo do Mac (atraso de ~1 dia)
]


# ===================== LEITURA DA CONFIG (Google Sheets) =====================

def ler_config():
    """Le a aba CONFIG da planilha publicada como CSV. Retorna so os ATIVOS."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
        f"/gviz/tq?tqx=out:csv&sheet={ABA_CONFIG}"
    )
    linhas = list(csv.DictReader(io.StringIO(_get(url))))

    ativos = []
    for r in linhas:
        # normaliza chaves (tira espacos) e valores
        r = {(k or "").strip().upper(): (v or "").strip() for k, v in r.items()}
        if r.get("ATIVO", "").lower() != "sim":
            continue
        # varios links na mesma celula (virgula/espaco) = mesmo conteudo publicado 2x: soma as views
        ids = [extrair_video_id(p) for p in re.split(r"[,;\s]+", r.get("VIDEO_ID", ""))]
        video_id = "+".join(i for i in ids if i)
        if not video_id:
            continue
        try:
            leads = int(float(r.get("LEADS", "0") or 0))
        except ValueError:
            leads = 0
        ativos.append({
            "lancamento": r.get("LANCAMENTO", ""),
            "tag": r.get("TAG", ""),
            "personagem": r.get("PERSONAGEM", ""),
            "cpl": normalizar_cpl(r.get("CPL", "")),
            "video_id": video_id,
            "leads": leads,
            "unicos": int(float(r.get("ESPECTADORES_UNICOS", "0") or 0)),
            "planilha_leads": r.get("PLANILHA_LEADS", ""),
        })
    return ativos


def normalizar_cpl(valor):
    """'1' -> '1' (CPL1) · 'AQ2', 'A2', 'Aquecimento 2' -> 'AQ2' (aquecimento)."""
    v = (valor or "").strip().upper()
    if v.startswith("A"):
        return "AQ" + re.sub(r"\D", "", v)
    return v


def rotulo(cpl):
    """Nome pra gente ler: 'AQ2' -> 'Aquecimento 2' · '1' -> 'CPL1'."""
    return f"Aquecimento {cpl[2:]}" if cpl.startswith("AQ") else f"CPL{cpl}"


def extrair_video_id(entrada):
    """Aceita ID puro, link normal, youtu.be, /live/ ou /embed/."""
    s = (entrada or "").strip()
    if not s:
        return ""
    if not re.search(r"[/.]", s):
        return s  # ja e o ID puro
    for padrao in (
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"/live/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
        r"([A-Za-z0-9_-]{11})",
    ):
        m = re.search(padrao, s)
        if m:
            return m.group(1)
    return ""


def base_de_leads(cfg, contagem):
    """Captacao fechada -> numero fixo · senao a contagem da planilha da agencia · senao a coluna LEADS."""
    return LEADS_FECHADOS.get(cfg["lancamento"]) or contagem or cfg["leads"]


def contar_leads_unicos(link):
    """
    Leads unicos = linhas preenchidas da aba LEADS UNIQUE da planilha de leads do lancamento
    (1 e-mail por linha; e o mesmo numero do dashboard da agencia). Precisa do usuario-robo
    com acesso de leitura a planilha. Devolve None se nao der (ai vale a coluna LEADS).
    """
    m = re.search(r"/d/([A-Za-z0-9_-]+)", link or "")
    if not (m and GOOGLE_SA_JSON):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SA_JSON), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    cred.refresh(Request())
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{m.group(1)}/values/"
           + urllib.parse.quote("'LEADS UNIQUE'!A2:A"))
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cred.token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            linhas = json.load(r).get("values", [])
    except urllib.error.HTTPError as e:
        print(f"Leads da planilha recusados ({e.code}): compartilhou com o usuario-robo?", file=sys.stderr)
        return None
    return sum(1 for l in linhas if l and l[0].strip())


# ===================== YOUTUBE =====================

def buscar_estatisticas(video_id):
    """
    Le as estatisticas publicas pela YouTube Data API (chave).
    'id1+id2' = mesmo conteudo em 2 videos: soma views/likes/comentarios e conta
    as horas a partir do primeiro publicado.
    """
    url = "https://www.googleapis.com/youtube/v3/videos"
    ids = video_id.split("+")
    params = {"part": "statistics,snippet,liveStreamingDetails", "id": ",".join(ids), "key": YOUTUBE_API_KEY}
    data = json.loads(_get(url, params))
    if data.get("error"):
        print(f"Erro YouTube API ({video_id}): {data['error']}", file=sys.stderr)
        return None
    itens = data.get("items", [])
    if not itens:
        return None
    if len(itens) < len(ids):
        print(f"Aviso: so {len(itens)} de {len(ids)} videos encontrados em {video_id}", file=sys.stderr)
    soma = lambda campo: sum(int(i.get("statistics", {}).get(campo, 0) or 0) for i in itens)
    # estreia/live: conta da hora real em que comecou (no ao vivo o publishedAt pode ainda ser a hora do agendamento)
    inicio = lambda i: i.get("liveStreamingDetails", {}).get("actualStartTime") or i.get("snippet", {}).get("publishedAt")
    pubs = [datetime.fromisoformat(p.replace("Z", "+00:00")) for p in map(inicio, itens) if p]
    return {
        "views": soma("viewCount"),
        "likes": soma("likeCount"),
        "comments": soma("commentCount"),
        "publishedAt": min(pubs) if pubs else None,
        # estreia (premiere) agendada: antes de comecar a API da 0 views e a data em que foi AGENDADA
        "agendado": all(i.get("snippet", {}).get("liveBroadcastContent") == "upcoming" for i in itens),
    }


# ===================== HISTORICO / COMPARACAO =====================

def ler_historico():
    if not os.path.exists(ARQUIVO_SNAPSHOTS):
        return []
    with open(ARQUIVO_SNAPSHOTS, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def gravar_snapshots(novas_linhas):
    existe = os.path.exists(ARQUIVO_SNAPSHOTS)
    os.makedirs(os.path.dirname(ARQUIVO_SNAPSHOTS), exist_ok=True)
    if existe:  # cabecalho ganhou colunas: reescreve so a 1a linha (linhas antigas ficam mais curtas)
        with open(ARQUIVO_SNAPSHOTS, encoding="utf-8") as f:
            linhas = f.read().split("\n", 1)
        if linhas[0].strip() != ",".join(CABECALHO_SNAPSHOTS):
            with open(ARQUIVO_SNAPSHOTS, "w", encoding="utf-8") as f:
                f.write(",".join(CABECALHO_SNAPSHOTS) + "\n" + (linhas[1] if len(linhas) > 1 else ""))
    with open(ARQUIVO_SNAPSHOTS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(CABECALHO_SNAPSHOTS)
        w.writerows(novas_linhas)


def ja_coletado(historico, lancamento, cpl, horas):
    return any(
        h.get("LANCAMENTO") == lancamento and str(h.get("CPL")) == str(cpl)
        and int(float(h.get("HORA_DESDE_PUBLICACAO") or -1)) == horas
        for h in historico
    )


# ===================== CURVA HORA A HORA (Studio) =====================
# dados/curvas_por_hora.csv: uma linha por video por hora, com as views do YouTube Studio
# (os dois lados da comparacao vem do Studio; o contador publico atrasa ate ~29% no dia 1).
# Os antigos foram puxados 1 vez; o robo do Mac atualiza so o lancamento atual de hora em hora.

ARQUIVO_CURVAS = os.path.join("dados", "curvas_por_hora.csv")
CABECALHO_CURVAS = ["LANCAMENTO", "PERSONAGEM", "CPL", "VIDEO_ID", "HORA", "VIEWS_ACUMULADAS", "LEADS", "PCT"]
HORAS_CURVA = 72


def linhas_da_curva(lancamento, personagem, cpl, video_id, views_por_hora, leads, ate=HORAS_CURVA):
    """
    views_por_hora = o que o Studio devolve (views DENTRO de cada hora, a partir da hora 0).
    HORA no CSV = horas completas desde a publicacao: HORA 24 = soma das primeiras 24 horas.
    PCT = acumulado / leads do PROPRIO lancamento (fracao, igual ao PCT_VIEWS dos snapshots).
    Zeros no fim = horas que o Studio ainda nao fechou: saem (nunca gravar zero de mentira).
    """
    v = list(views_por_hora[:ate])
    while v and v[-1] == 0:
        v.pop()
    linhas, total = [], 0
    for i, x in enumerate(v):
        total += x
        linhas.append([lancamento, personagem, cpl, video_id, i + 1, total, leads,
                       f"{total / leads:.6f}" if leads else ""])
    return linhas


def juntar_curvas(antigas, novas):
    """
    Troca no CSV so os videos (LANCAMENTO + CPL) que vieram com dado agora; o resto fica como estava.
    Video que falhou nao entra em 'novas' -> a curva dele no CSV continua intacta.
    """
    trocar = {(str(l[0]), str(l[2])) for l in novas}
    ficam = [l for l in antigas if (str(l[0]), str(l[2])) not in trocar]
    return sorted(ficam + novas, key=lambda l: (str(l[0]), str(l[2]), int(l[4])))


def ler_curvas(arquivo=ARQUIVO_CURVAS):
    if not os.path.exists(arquivo):
        return []
    with open(arquivo, newline="", encoding="utf-8") as f:
        return [[r[c] for c in CABECALHO_CURVAS] for r in csv.DictReader(f)]


def gravar_curvas(linhas, arquivo=ARQUIVO_CURVAS):
    with open(arquivo, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CABECALHO_CURVAS)
        w.writerows(linhas)


# ===================== TELEGRAM =====================

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram nao configurado.", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # HTML (<b>) e nao Markdown: '_' num nome derrubava o envio; em HTML so & < > precisam de escape
    _post(url, {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"})


def mil(n):
    """38.312 -> '38,3 mil' · 24.000 -> '24 mil' · 812 -> '812'."""
    n = int(n)
    return str(n) if n < 1000 else f"{n / 1000:.1f}".replace(".", ",").removesuffix(",0") + " mil"


def variacao(x):
    """-0.27 -> '−27%' · 0.05 -> '+5%'."""
    return f"{x * 100:+.0f}%".replace("-", "−")


# ponytail: artigo por nome; oficina nova com nome feminino entra aqui
FEMININAS = {"BIANCA", "BRANCA DE NEVE", "MORANGUINHO", "EMÍLIA"}


def com_artigo(personagem):
    """'BIANCA' -> 'da Bianca' · 'COELHO FOFUXO' -> 'do Coelho Fofuxo'."""
    p = personagem.strip().upper()
    nome = p.title().replace(" De ", " de ").replace("Auau", "AUAU")
    return ("da " if p in FEMININAS else "do ") + html.escape(nome, quote=False)


def pct_na_hora(curvas, cpl, hora):
    """{lancamento: (personagem, pct)} de todo lancamento que tem o CPLn fechado naquela hora (Studio)."""
    return {l[0]: (l[1], float(l[7])) for l in curvas
            if str(l[2]) == str(cpl) and int(l[4]) == hora and l[7] != ""}


def ultima_hora(curvas, lancamento, cpl):
    horas = [int(l[4]) for l in curvas if l[0] == lancamento and str(l[2]) == str(cpl) and l[7] != ""]
    return max(horas) if horas else None


def linha_vizinhos(curvas, lancamento, cpl):
    """
    Ranking do CPLn na ultima hora com Studio do atual (CPLn x CPLn, mesma hora desde a publicacao):
    quem esta logo acima e logo abaixo. Sem curva ou sem antigo nessa hora -> None (nunca inventar).
    """
    hora = ultima_hora(curvas, lancamento, cpl)
    todos = pct_na_hora(curvas, cpl, hora) if hora else {}
    if lancamento not in todos or len(todos) < 2:
        return None
    ordem = sorted(todos, key=lambda k: -todos[k][1])
    i = ordem.index(lancamento)
    if i == 0:
        return "✅ Acima de todos"
    acima = com_artigo(todos[ordem[i - 1]][0])
    if i == len(ordem) - 1:
        return f"⚠️ Último, abaixo {acima}"
    return f"✅ Acima {com_artigo(todos[ordem[i + 1]][0])}, abaixo {acima}"


def linhas_media_e_queda(curvas, lancamento, cpls):
    """
    Na ultima hora do CPL mais novo: media dos CPLs do atual x a de cada oficina que tem TODOS esses CPLs
    nessa hora, e a queda do penultimo pro ultimo CPL. Sem oficina comparavel -> nenhuma linha.
    """
    hora = ultima_hora(curvas, lancamento, cpls[-1])
    if not hora:
        return []
    por_cpl = [pct_na_hora(curvas, c, hora) for c in cpls]
    comuns = [k for k in por_cpl[0] if all(k in p for p in por_cpl)]
    if lancamento not in comuns or len(comuns) < 2:
        return []
    media = {k: sum(p[k][1] for p in por_cpl) / len(cpls) for k in comuns}
    pos = 1 + sum(1 for k in comuns if media[k] > media[lancamento])
    linhas = [f"📍 <b>Média dos CPLs:</b> {pos}º de {len(comuns)} lançamentos"]
    # oficina com 0% no CPL anterior nao tem queda (dividiria por zero): fica fora so dessa conta
    queda = {k: por_cpl[-1][k][1] / por_cpl[-2][k][1] - 1 for k in comuns if por_cpl[-2][k][1] > 0} \
        if len(cpls) >= 2 else {}
    outras = [queda[k] for k in queda if k != lancamento]
    if lancamento in queda and outras:
        q = queda[lancamento]
        if q > max(outras):
            fim = "(a menor de todas 👏)"
        elif q < min(outras):
            fim = "(a maior de todas ⚠️)"
        else:
            fim = f"(média {variacao(sum(outras) / len(outras))})"
        linhas.append(f"📉 <b>Queda do {cpls[-2]} pro {cpls[-1]}:</b> {variacao(q)} {fim}")
    return linhas


def montar_mensagem(lancamento, ativos, historico, curvas, agora):
    """
    So o lancamento atual, so aquecimentos e CPLs. Views = contador publico mais recente (snapshots);
    comparacoes = Studio x Studio na mesma hora (curvas_por_hora); unicos = CONFIG (Studio, ~1 dia de atraso).
    """
    def views(cfg):
        ult = [h for h in historico if h.get("LANCAMENTO") == lancamento and str(h.get("CPL")) == cfg["cpl"]]
        return ult[-1] if ult else None

    meus = [c for c in ativos if c["lancamento"] == lancamento]
    # aquecimentos na ordem de publicacao (mais antigo primeiro), pelo nome da CONFIG: o codigo AQn nao segue a ordem
    aqs = sorted((c for c in meus if c["cpl"].startswith("AQ") and views(c)),
                 key=lambda c: -int(float(views(c).get("HORA_DESDE_PUBLICACAO") or 0)))
    cpls = sorted((c for c in meus if c["cpl"].isdigit()), key=lambda c: int(c["cpl"]))
    cpls = [c for c in cpls if views(c)]

    e = lambda t: html.escape(str(t), quote=False)  # nome da CONFIG pode ter & < >
    blocos = [f"📊 <b>{e(lancamento)} · {agora.strftime('%d/%m %H')}h</b>"]
    if aqs:
        blocos.append("🔥 <b>AQ</b>\n" + "\n".join(f"{e(c['personagem'])}: {mil(views(c)['VIEWS'])}" for c in aqs))
    for c in cpls:
        v = views(c)
        b = f"🎬 <b>CPL {e(c['cpl'])}</b>\n{mil(v['VIEWS'])} views"
        if v.get("PCT_VIEWS"):
            b += f" ({float(v['PCT_VIEWS']) * 100:.0f}% dos inscritos)"
        if l := linha_vizinhos(curvas, lancamento, c["cpl"]):
            b += "\n" + l
        b += "\n👤 Únicos: " + (mil(c["unicos"]) if c["unicos"] else "amanhã")
        blocos.append(b)
    if cpls and (fim := linhas_media_e_queda(curvas, lancamento, [c["cpl"] for c in cpls])):
        blocos.append("\n".join(fim))
    msg = "\n\n".join(blocos) + "\n"
    return msg


# ===================== PRINCIPAL =====================

def main():
    if not YOUTUBE_API_KEY:
        print("Falta a YOUTUBE_API_KEY.", file=sys.stderr)
        sys.exit(1)

    ativos = ler_config()
    if not ativos:
        print("Nenhum CPL ativo na planilha. Nada a fazer.")
        return

    # leads da planilha da agencia (1 leitura por planilha); sem acesso, vale a coluna LEADS da CONFIG
    contagens = {}
    for cfg in ativos:
        link = cfg["planilha_leads"]
        if cfg["lancamento"] not in LEADS_FECHADOS and link and link not in contagens:
            contagens[link] = contar_leads_unicos(link)
        cfg["leads"] = base_de_leads(cfg, contagens.get(link))
    print(f"Leads unicos da planilha: {contagens or 'coluna PLANILHA_LEADS vazia'}")

    curvas = ler_curvas()
    historico = ler_historico()
    agora = datetime.now(timezone.utc)
    agora_br = agora.astimezone(FUSO)

    novas_linhas = []
    itens_resumo = []

    for cfg in ativos:
        stats = buscar_estatisticas(cfg["video_id"])
        if not stats or not stats["publishedAt"]:
            print(f"Video {cfg['video_id']} ({cfg['lancamento']} {rotulo(cfg['cpl'])}) nao encontrado: "
                  "privado ou apagado? Desmarque ATIVO na planilha.", file=sys.stderr)
            continue
        if stats.get("agendado") or stats["publishedAt"] > agora:
            # sem linha no snapshots: a mensagem nao ganha bloco de 0 views e a hora 0 conta da estreia
            print(f"{cfg['lancamento']} {rotulo(cfg['cpl'])} ainda nao estreou: fica pra proxima rodada.")
            continue
        horas = int((agora - stats["publishedAt"]).total_seconds() // 3600)
        if ja_coletado(historico, cfg["lancamento"], cfg["cpl"], horas):
            continue  # o workflow roda 2x/hora; a 2a vez nao repete coleta nem Telegram
        pct = (stats["views"] / cfg["leads"]) if cfg["leads"] > 0 else None

        linha = [
            agora_br.strftime("%Y-%m-%d %H:%M:%S"), cfg["lancamento"], cfg["tag"],
            cfg["cpl"], cfg["video_id"], horas, stats["views"], stats["likes"],
            stats["comments"], "" if pct is None else f"{pct:.6f}",
            cfg["unicos"] or "", f"{cfg['unicos'] / cfg['leads']:.6f}" if cfg["unicos"] and cfg["leads"] else "",
        ]
        novas_linhas.append(linha)
        historico.append(dict(zip(CABECALHO_SNAPSHOTS, map(str, linha))))  # a mensagem usa a coleta de agora
        itens_resumo.append(cfg)

    if novas_linhas:
        gravar_snapshots(novas_linhas)
    if itens_resumo:
        # lancamento atual = o da ultima linha ATIVA da CONFIG
        enviar_telegram(montar_mensagem(ativos[-1]["lancamento"], ativos, historico, curvas, agora_br))
        print(f"OK: {len(itens_resumo)} video(s) coletados e enviados ao Telegram.")


if __name__ == "__main__":
    main()
