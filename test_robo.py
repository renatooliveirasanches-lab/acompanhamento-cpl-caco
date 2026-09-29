"""Checagem da comparacao com a regua. Roda com: python3 test_robo.py"""

import tempfile
import robo

REGUA = {
    "1": [
        {"lancamento": "novembro 2024", "personagem": "ANJINHO", "leads": 41924, "pct": 1.0714},
        {"lancamento": "junho 2026", "personagem": "MORANGUINHO", "leads": 65826, "pct": 0.583},
        {"lancamento": "janeiro 2026", "personagem": "FOFUXO", "leads": 54196, "pct": 0.450},
    ],
    "2": [{"lancamento": "julho 2024", "personagem": "SUPER ALPHA", "leads": 21835, "pct": 0.503}],
}
MEDIA_CPL1 = (1.0714 + 0.583 + 0.450) / 3  # 0.7015


def test_compara_o_mesmo_cpl():
    """CPL1 so pode ser comparado com CPL1 — nunca com o CPL2."""
    c = robo.comparar_com_regua(REGUA, "1", 0.70, 62866, 30)
    assert c["qtd"] == 3, "devia usar as 3 oficinas do CPL1"
    assert abs(c["media"] - MEDIA_CPL1) < 1e-6
    c2 = robo.comparar_com_regua(REGUA, "2", 0.50, 62866, 30)
    assert c2["qtd"] == 1, "CPL2 tem regua propria"


def test_parcial_antes_de_24h():
    """Antes de fechar o dia 1 nao existe veredito — so parcial."""
    c = robo.comparar_com_regua(REGUA, "1", 0.294, 62866, 10)
    assert c["status"] == "PARCIAL" and not c["fechou"]
    # mesmo um numero altissimo continua parcial: a janela nao fechou
    assert robo.comparar_com_regua(REGUA, "1", 2.0, 62866, 23)["status"] == "PARCIAL"


def test_veredito_depois_de_24h():
    """O veredito usa o % CONGELADO nas 24h, nao o acumulado de agora."""
    assert robo.comparar_com_regua(REGUA, "1", 0.90, 62866, 24, 0.90)["status"] == "ACIMA"
    assert robo.comparar_com_regua(REGUA, "1", 0.40, 62866, 24, 0.40)["status"] == "ABAIXO"
    assert robo.comparar_com_regua(REGUA, "1", 0.70, 62866, 24, MEDIA_CPL1)["status"] == "IGUAL"


def test_nao_repete_a_mesma_hora():
    """O workflow roda 2x por hora: a 2a execucao nao pode duplicar a coleta."""
    h = [{"LANCAMENTO": "LC0526", "CPL": "1", "HORA_DESDE_PUBLICACAO": "5"}]
    assert robo.ja_coletado(h, "LC0526", "1", 5)
    assert not robo.ja_coletado(h, "LC0526", "1", 6)
    assert not robo.ja_coletado(h, "LC0526", "2", 5)


def test_nao_compara_77h_contra_regua_de_24h():
    """
    O bug real de 30/07: o video tinha 114,3% acumulado em 77h e o robo
    dizia ACIMA da media de 70,1% — mas no fim do dia 1 tinha 56,1%,
    que e ABAIXO. O veredito tem que seguir o numero das 24h.
    """
    c = robo.comparar_com_regua(REGUA, "1", 1.143, 62866, 77, pct_fechamento=0.561)
    assert c["status"] == "ABAIXO", "veredito deve usar as 24h, nao as 77h"
    assert c["pct_veredito"] == 0.561


def test_sem_fechamento_nao_da_veredito():
    """Passou de 24h mas nao achou o snapshot do fechamento: segura o veredito."""
    c = robo.comparar_com_regua(REGUA, "1", 1.143, 62866, 77, pct_fechamento=None)
    assert c["status"] == "PARCIAL" and not c["fechou"]


def test_pct_no_fechamento_pega_o_snapshot_certo():
    hist = [
        {"LANCAMENTO": "LC0426", "CPL": "1", "HORA_DESDE_PUBLICACAO": "10", "PCT_VIEWS": "0.294"},
        {"LANCAMENTO": "LC0426", "CPL": "1", "HORA_DESDE_PUBLICACAO": "24", "PCT_VIEWS": "0.561"},
        {"LANCAMENTO": "LC0426", "CPL": "1", "HORA_DESDE_PUBLICACAO": "77", "PCT_VIEWS": "1.143"},
        {"LANCAMENTO": "LC0426", "CPL": "2", "HORA_DESDE_PUBLICACAO": "24", "PCT_VIEWS": "0.30"},
        {"LANCAMENTO": "OUTRO", "CPL": "1", "HORA_DESDE_PUBLICACAO": "24", "PCT_VIEWS": "0.99"},
    ]
    assert robo.pct_no_fechamento(hist, "LC0426", "1") == (0.561, 24)
    assert robo.pct_no_fechamento(hist, "LC0426", "2") == (0.30, 24)
    assert robo.pct_no_fechamento(hist, "INEXISTENTE", "1") is None


def test_escolhe_a_base_mais_parecida():
    """62.866 leads -> MORANGUINHO (65.826) e o comparavel justo."""
    c = robo.comparar_com_regua(REGUA, "1", 0.30, 62866, 10)
    assert c["parecido"]["personagem"] == "MORANGUINHO"
    assert c["melhor"]["personagem"] == "ANJINHO"
    assert c["pior"]["personagem"] == "FOFUXO"


def test_sem_regua_nao_quebra():
    assert robo.comparar_com_regua(REGUA, "9", 0.30, 62866, 10) is None
    assert robo.comparar_com_regua(REGUA, "1", None, 62866, 10) is None
    assert robo.comparar_com_regua({}, "1", 0.30, 62866, 10) is None


def test_mensagem_sai_inteira():
    comp = robo.comparar_com_regua(REGUA, "1", 0.294, 62866, 10)
    msg = robo.montar_mensagem([{
        "lancamento": "LC0426", "personagem": "Fofuxo AUAU", "cpl": "1",
        "horas": 10, "views": 18476, "pct": 0.294, "comp": comp,
    }], __import__("datetime").datetime(2026, 7, 27, 18, 6))
    assert "29,4%" in msg and "MORANGUINHO" in msg and "faltam 14h" in msg
    print(msg)


if __name__ == "__main__":
    for nome, fn in sorted(globals().items()):
        if nome.startswith("test_"):
            fn()
            print(f"ok  {nome}")
    print("\nTudo passou.")


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
