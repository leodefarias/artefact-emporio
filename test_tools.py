"""Testa as ferramentas contra as armadilhas reais dos dados. Não precisa de LLM."""
from datetime import date

from tools import buscar_produtos, consultar_pedido

HOJE = date(2026, 10, 31)


def _um(termo):
    r = buscar_produtos(termo=termo)
    assert r["total"] == 1, r
    return r["produtos"][0]


def test_promocao_inativa_nao_vale_e_pix_aplica():
    p = _um("Takamine GD20")
    assert p["preco_tabela"] == "R$ 2.199,00"
    assert p["preco_pix"] == "R$ 2.089,05"
    assert "desconto" not in p


def test_promocao_ativa_mostra_original_e_percentual_sem_pix():
    p = _um("Taylor 110e")
    assert p["desconto"] == "8%"
    assert p["preco_promocional"] != p["preco_original"]
    assert "preco_pix" not in p


def test_violoes_ate_1000_excluem_esgotado_e_descontinuado():
    r = buscar_produtos(categoria="Violões", preco_max=1000)
    nomes = [p["nome"] for p in r["produtos"]]
    assert r["total"] == len(nomes) == 12
    assert not any("GF-3D" in n or "SN-7C" in n for n in nomes)


def test_busca_por_nome_mostra_esgotado_e_descontinuado():
    assert _um("GF-3D")["situacao"] == "esgotado"
    p = _um("SN-7C")
    assert p["situacao"] == "descontinuado"
    assert "preco_tabela" not in p


def test_descontinuado_com_estoque_nao_conta_como_disponivel():
    r = buscar_produtos(termo="Ibanez Bass 2X", so_disponiveis=True)
    assert r["total"] == 0


def test_em_breve_nao_conta_como_disponivel():
    assert _um("Kala KA-ABP-CTG")["situacao"] == "em breve"
    assert buscar_produtos(termo="Kala KA-ABP-CTG", so_disponiveis=True)["total"] == 0


def test_busca_ignora_acento_caixa_e_pontuacao():
    assert _um("takamine gd-20")["nome"] == "Takamine GD20 Dreadnought Natural"
    assert buscar_produtos(termo="VIOLÃO")["total"] > 0


def test_so_promocao_retorna_apenas_as_quatro_ativas():
    r = buscar_produtos(so_promocao=True)
    assert r["total"] == 4
    assert all("desconto" in p for p in r["produtos"])


def test_categoria_sem_produto_e_categoria_invalida():
    assert buscar_produtos(categoria="Cordas Orquestrais")["total"] == 0
    assert "erro" in buscar_produtos(categoria="Sanfonas")


def test_argumentos_vindos_do_llm_como_texto():
    assert buscar_produtos(categoria="Violões", preco_max="1000", termo="")["total"] == 12
    assert "erro" in buscar_produtos(preco_max="barato")


def test_pedido_exige_contato_e_nao_revela_existencia():
    negado = consultar_pedido(1, "errado@x.com", hoje=HOJE)
    assert negado == consultar_pedido(9999, "errado@x.com", hoje=HOJE)
    assert negado == consultar_pedido(1, "", hoje=HOJE)
    assert negado == consultar_pedido("abc", "x", hoje=HOJE)
    assert "status" not in negado


def test_pedido_aceita_email_ou_telefone_em_qualquer_formato():
    assert consultar_pedido(4, "Lucas.Mendes@JMAIL.com ", hoje=HOJE)["cliente"] == "Lucas"
    assert consultar_pedido("4", "+55 67 99812-3456", hoje=HOJE)["cliente"] == "Lucas"


def test_pedido_nao_expoe_dados_pessoais():
    texto = str(consultar_pedido(4, "(67) 99812-3456", hoje=HOJE))
    for dado in ("Mendes", "jmail", "99812", "Campo Grande"):
        assert dado not in texto


def test_pedido_calcula_dias_e_atraso():
    p = consultar_pedido(8, "anacarol.ferreira@coldmail.com", hoje=HOJE)
    assert p["status"] == "enviado"
    assert p["dias_desde_compra"] == (HOJE - date(2026, 2, 15)).days
    assert p["entrega_atrasada"] is True
    assert p["itens"] == [{"nome": p["itens"][0]["nome"], "quantidade": 1}]
    antes = consultar_pedido(8, "anacarol.ferreira@coldmail.com", hoje=date(2026, 2, 20))
    assert antes["entrega_atrasada"] is False


def test_pedido_entregue_nao_fica_atrasado_e_cancelado_traz_motivo():
    entregue = consultar_pedido(4, "lucas.mendes@jmail.com", hoje=HOJE)
    assert entregue["status"] == "entregue" and entregue["entrega_atrasada"] is False
    assert "antes do processamento" in consultar_pedido(17, _email(15), hoje=HOJE)["observacoes"]


def _email(customer_id):
    from tools import CLIENTES
    return CLIENTES[str(customer_id)]["email"]
