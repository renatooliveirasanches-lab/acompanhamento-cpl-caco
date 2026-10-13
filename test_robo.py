"""Checagem da comparacao com a regua. Roda com: python3 test_robo.py"""

import tempfile
import robo

def test_nao_repete_a_mesma_hora():
    """O workflow roda 2x por hora: a 2a execucao nao pode duplicar a coleta."""
    h = [{"LANCAMENTO": "LC0526", "CPL": "1", "HORA_DESDE_PUBLICACAO": "5"}]
    assert robo.ja_coletado(h, "LC0526", "1", 5)
    assert not robo.ja_coletado(h, "LC0526", "1", 6)
    assert not robo.ja_coletado(h, "LC0526", "2", 5)


def test_aquecimento_tem_nome_proprio_e_nao_mistura_com_cpl():
    assert robo.normalizar_cpl("Aquecimento 2") == "AQ2"
    assert robo.normalizar_cpl("a2") == "AQ2"
    assert robo.normalizar_cpl("2") == "2"
    assert robo.rotulo("AQ2") == "Aquecimento 2"
    assert robo.rotulo("2") == "CPL2"


def test_dois_videos_do_mesmo_aquecimento_somam():
    resposta = {"items": [
        {"statistics": {"viewCount": "100", "likeCount": "5"}, "snippet": {"publishedAt": "2026-09-27T12:00:00Z"}},
        {"statistics": {"viewCount": "50", "likeCount": "1"}, "snippet": {"publishedAt": "2026-09-27T10:00:00Z"}},
    ]}
    original = robo._get
    robo._get = lambda url, params=None, timeout=30: robo.json.dumps(resposta)
    try:
        st = robo.buscar_estatisticas("aaaaaaaaaaa+bbbbbbbbbbb")
    finally:
        robo._get = original
    assert st["views"] == 150 and st["likes"] == 6
    assert st["publishedAt"].hour == 10  # conta do primeiro publicado


def test_cabecalho_antigo_ganha_colunas_novas(tmp=None):

    pasta = tempfile.mkdtemp()
    arq = robo.os.path.join(pasta, "snap.csv")
    open(arq, "w").write("TIMESTAMP,LANCAMENTO\nx,y\n")
    original = robo.ARQUIVO_SNAPSHOTS
    robo.ARQUIVO_SNAPSHOTS = arq
    try:
        robo.gravar_snapshots([["a"] * len(robo.CABECALHO_SNAPSHOTS)])
    finally:
        robo.ARQUIVO_SNAPSHOTS = original
    linhas = open(arq).read().splitlines()
    assert linhas[0] == ",".join(robo.CABECALHO_SNAPSHOTS)
    assert linhas[1] == "x,y" and len(linhas) == 3


def test_curva_por_hora_pct_e_juncao_atual_x_antigos():
    """
    % da curva = acumulado / leads do PROPRIO lancamento; HORA 2 = soma das 2 primeiras horas.
    A comparacao e CPLn x CPLn na MESMA hora; atualizar o atual nao mexe nos antigos;
    video que falhou (sem linhas novas) nao apaga a curva que ja estava gravada.
    """
    l = robo.linhas_da_curva("LC0526", "Emilia", "1", "x", [2000, 1000, 0, 0], 10000)
    assert [(r[4], r[5], r[7]) for r in l] == [(1, 2000, "0.200000"), (2, 3000, "0.300000")], l  # zeros do fim saem
    assert robo.linhas_da_curva("LC0526", "Emilia", "1", "x", [5] * 100, 10)[-1][4] == 72  # corta em 72h

    antigos = (robo.linhas_da_curva("nov 2024", "ANJINHO", "1", "a", [4000, 4000], 20000)      # 40% na hora 2
               + robo.linhas_da_curva("jun 2026", "MORANGUINHO", "1", "m", [1000, 1000], 10000)  # 20% na hora 2
               + robo.linhas_da_curva("jun 2026", "MORANGUINHO", "2", "m2", [9000, 9000], 10000))  # CPL2: fica fora
    csv_ = robo.juntar_curvas(antigos, l)
    assert robo.linha_vizinhos(csv_, "LC0526", "1") == "✅ Acima da Moranguinho, abaixo do Anjinho", \
        "30% na hora 2: entre 40% e 20%, sem o CPL2"

    # nova hora do atual: troca so as linhas dele
    novo = robo.linhas_da_curva("LC0526", "Emilia", "1", "x", [2000, 1000, 500], 10000)
    csv2 = robo.juntar_curvas(csv_, novo)
    assert len(csv2) == len(antigos) + 3 and [r for r in csv2 if r[0] != "LC0526"] == sorted(antigos, key=lambda r: (r[0], r[2], r[4]))
    # falha (nenhuma linha nova) nao apaga nada
    assert robo.juntar_curvas(csv2, []) == csv2
    # hora 3: nenhum antigo tem -> sem comparacao (nunca inventar)
    assert robo.linha_vizinhos(csv2, "LC0526", "1") is None


def test_push_do_mac_sobe_commit_parado_e_avisa_quando_falha():
    """Repositorio de mentira (nada vai pro GitHub): commit parado sobe; sem remoto, sai com erro."""
    import os, subprocess, sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ROBO-MAC"))
    import curva_por_hora as cph
    pasta = tempfile.mkdtemp()
    sh = lambda *c: subprocess.run(c, cwd=pasta, capture_output=True, check=True)
    sh("git", "init", "-q", "--bare", "remoto.git")
    sh("git", "clone", "-q", "remoto.git", "copia")
    original = cph.REPO
    cph.REPO = os.path.join(pasta, "copia")
    try:
        for c in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            assert cph.git(*c).returncode == 0
        cph.git("push", "-q", "-u", "origin", "HEAD")
        assert not cph.commits_parados()
        cph.git("commit", "-q", "--allow-empty", "-m", "parado")
        assert cph.commits_parados(), "commit novo tem que contar como parado"
        assert cph.publicar() and not cph.commits_parados()
        cph.git("commit", "-q", "--allow-empty", "-m", "sem remoto")
        cph.git("remote", "set-url", "origin", os.path.join(pasta, "nao-existe.git"))
        assert cph.publicar() is False, "push que falha tem que devolver False (saida 1 no log)"
    finally:
        cph.REPO = original


def test_leads_da_planilha_antiga_trocados_pelos_certos():
    """CPL - ATUALIZADO tem LEADS copiados; a tabela de correcao vence, o resto fica como na planilha."""
    import levantar_historico as lh
    linha = ["setembro 2024", "OSP AGO24", "link", "60.806", "", "", "", "", "CAPITÃO AMÉRICA "]
    assert lh.leads(linha) == 44565, "personagem com espaco no fim tambem tem que achar a correcao"
    assert lh.leads(["agosto 2024", "", "", "46.273", "", "", "", "", "HARRY POP"]) == 46273
    fofuxo = ["janeiro 2026", "", "", "54.196", "", "", "", "", "FOFUXO"]
    assert lh.nome(fofuxo) == ("fevereiro 2026", "COELHO FOFUXO") and lh.leads(fofuxo) == 54195
    cur = robo.ler_curvas()
    assert {int(l[6]) for l in cur if l[1] == "ANJINHO"} == {61757}, "curvas_por_hora.csv sem o numero certo"


def test_captacao_fechada_usa_base_fixa():
    """LC0526 fechou em 63.271: a contagem da agencia (63.325) e a coluna LEADS nao mandam mais."""
    fechado = {"lancamento": "LC0526", "leads": 0}
    assert robo.base_de_leads(fechado, 63325) == 63271
    assert robo.base_de_leads(fechado, None) == 63271
    aberto = {"lancamento": "LC0626", "leads": 50000}
    assert robo.base_de_leads(aberto, 51234) == 51234, "captacao aberta: vale a planilha da agencia"
    assert robo.base_de_leads(aberto, None) == 50000, "sem planilha: vale a coluna LEADS"

def test_mensagem_nova_formato_aprovado():
    """
    So o lancamento atual, so aquecimentos e CPLs. Exemplo com 3 oficinas antigas na hora 2:
    CPL2: Bianca 40 · Emilia 35 · Chapeuzinho 20 · Coelho 10. Media na hora 2: 50 / 42,5 / 30 / 20 -> 2o de 4.
    Queda Emilia -30% x antigas -33%, -50%, -67% -> a menor.
    """
    import datetime
    curvas = []
    for lanc, nome, c1, c2 in [("LC0526", "Emília - Aula 1", 5000, 3500), ("mar 2026", "BIANCA", 6000, 4000),
                               ("set 2025", "CHAPÉUZINHO", 4000, 2000), ("fev 2026", "COELHO FOFUXO", 3000, 1000)]:
        curvas += robo.linhas_da_curva(lanc, nome, "1", "x", [c1 / 2, c1 / 2], 10000)
        curvas += robo.linhas_da_curva(lanc, nome, "2", "x", [c2 / 2, c2 / 2], 10000)
    curvas.append(["LC0526", "Emília - Aula 1", "1", "x", 3, 5500, 10000, "0.550000"])  # CPL1 ja na hora 3
    ativos = [
        {"lancamento": "LC0426", "cpl": "1", "unicos": 0},                      # outro lancamento: some
        {"lancamento": "LC0526", "cpl": "AQ0", "personagem": "Tiara", "unicos": 0},
        {"lancamento": "LC0526", "cpl": "AQ1", "personagem": "Porquinho 1", "unicos": 900},
        {"lancamento": "LC0526", "cpl": "1", "unicos": 24040},
        {"lancamento": "LC0526", "cpl": "2", "unicos": 0},
    ]
    hist = [
        {"LANCAMENTO": "LC0526", "CPL": "AQ0", "VIEWS": "12000", "HORA_DESDE_PUBLICACAO": "137"},
        {"LANCAMENTO": "LC0526", "CPL": "AQ0", "VIEWS": "12345", "HORA_DESDE_PUBLICACAO": "138"},  # o mais recente vale
        {"LANCAMENTO": "LC0526", "CPL": "AQ1", "VIEWS": "94593", "HORA_DESDE_PUBLICACAO": "370"},
        {"LANCAMENTO": "LC0526", "CPL": "1", "VIEWS": "38312", "PCT_VIEWS": "0.605"},
        {"LANCAMENTO": "LC0526", "CPL": "2", "VIEWS": "11841", "PCT_VIEWS": "0.187"},
        {"LANCAMENTO": "LC0426", "CPL": "1", "VIEWS": "99999", "PCT_VIEWS": "0.9"},
    ]
    msg = robo.montar_mensagem("LC0526", ativos, hist, curvas, datetime.datetime(2026, 10, 13, 17, 6))
    print(msg)
    assert "99" not in msg and "LC0426" not in msg, "outro lancamento nao entra"
    assert msg == (
        "📊 <b>LC0526 · 13/10 17h</b>\n\n"
        "🔥 <b>AQ</b>\nPorquinho 1: 94,6 mil\nTiara: 12,3 mil\n\n"   # ordem de publicacao, nome, contador mais recente
        "🎬 <b>CPL 1</b>\n38,3 mil views (60% dos inscritos)\n👤 Únicos: 24 mil\n\n"  # hora 3 sem antigo: sem ✅
        "🎬 <b>CPL 2</b>\n11,8 mil views (19% dos inscritos)\n✅ Acima do Chapéuzinho, abaixo da Bianca\n"
        "👤 Únicos: amanhã\n\n"
        "📍 <b>Média dos CPLs:</b> 2º de 4 lançamentos\n"
        "📉 <b>Queda do 1 pro 2:</b> −30% (a menor de todas 👏)\n"), msg

    # queda que nao e extrema mostra a media das outras: Bianca -33% x -30, -50, -67
    assert robo.linhas_media_e_queda(curvas, "mar 2026", ["1", "2"])[1] == "📉 <b>Queda do 1 pro 2:</b> −33% (média −49%)"
    # lanterna e primeiro
    assert robo.linha_vizinhos(curvas, "fev 2026", "2") == "⚠️ Último, abaixo do Chapéuzinho"
    assert robo.linha_vizinhos(curvas, "mar 2026", "1") == "✅ Acima de todos"
    # so CPL1 e nada de curva: sem linhas de analise inventadas
    so1 = robo.montar_mensagem("LC0526", ativos[:4], hist, [], datetime.datetime(2026, 10, 13, 17, 6))
    assert "✅" not in so1 and "📍" not in so1 and "📉" not in so1 and "👤 Únicos: 24 mil" in so1

    # oficina antiga com 0% no CPL1: nao derruba a rodada; so sai da conta da queda (media continua)
    zero = curvas + robo.linhas_da_curva("jan 2025", "ROBIN", "2", "x", [500, 500], 10000) \
        + [["jan 2025", "ROBIN", "1", "x", 2, 0, 10000, "0.000000"]]
    mq = robo.linhas_media_e_queda(zero, "LC0526", ["1", "2"])
    assert mq == ["📍 <b>Média dos CPLs:</b> 2º de 5 lançamentos", "📉 <b>Queda do 1 pro 2:</b> −30% (a menor de todas 👏)"], mq
    # o proprio lancamento com 0% no CPL1: sem linha de queda, sem erro
    assert len(robo.linhas_media_e_queda(zero, "jan 2025", ["1", "2"])) == 1


def test_nomes_com_simbolos_nao_quebram_o_html():
    """Telegram em HTML: & < > no nome viram &amp; &lt; &gt;; '_' passa direto (era o que derrubava o Markdown)."""
    import datetime
    ativos = [{"lancamento": "LC0526", "cpl": "AQ1", "personagem": "Tom_&_Jerry <2>", "unicos": 0}]
    hist = [{"LANCAMENTO": "LC0526", "CPL": "AQ1", "VIEWS": "1500", "HORA_DESDE_PUBLICACAO": "5"}]
    msg = robo.montar_mensagem("LC0526", ativos, hist, [], datetime.datetime(2026, 10, 13, 17, 6))
    assert "Tom_&amp;_Jerry &lt;2&gt;: 1,5 mil" in msg, msg
    assert robo.com_artigo("A & B") == "do A &amp; B"

if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print(f"ok  {nome}")
    print("\nTudo passou.")
