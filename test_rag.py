"""Testa o chunking (offline) e a recuperação (com servidor local), antes de ligar o LLM."""
import pytest

import rag


def _servidor_tem_embedding():
    try:
        rag.embed(["teste"])
        return True
    except Exception:
        return False


precisa_servidor = pytest.mark.skipif(
    not _servidor_tem_embedding(),
    reason=f"modelo {rag.EMBED_MODEL} indisponível em {rag.BASE_URL}",
)


def test_chunking_nao_perde_palavra_e_respeita_limites():
    # O chunking original do notebook perdia a palavra que estourava o limite.
    palavras = [f"palavra{i}" for i in range(1000)]
    chunks = rag.chunk_pages([(1, " ".join(palavras))])
    assert len(chunks) > 1
    assert all(len(texto) <= rag.CHUNK_CHARS for _, texto in chunks)
    vistas = {p for _, texto in chunks for p in texto.split()}
    assert vistas == set(palavras)
    # Há sobreposição entre chunks vizinhos.
    assert set(chunks[0][1].split()) & set(chunks[1][1].split())


def test_limpeza_do_manual():
    paginas = dict(rag.extract_pages(rag.PDF))
    texto = " ".join(paginas.values())
    assert "Página 2" not in texto and "Manual de Políticas e Procedimentos\n" not in paginas[2]
    assert "3321-4500" not in texto and "empóriodamusica" not in texto
    assert "Parcela mínima de R$ 100,00" not in texto
    assert "parcela mínima de R$ 100,00" in texto  # regra do §3.1 permanece


@precisa_servidor
@pytest.mark.parametrize("pergunta, paginas", [
    ("Qual o horário de funcionamento no sábado?", {2}),
    ("Qual o endereço da loja?", {1, 2}),
    ("Vocês vendem cordas e palhetas?", {2, 6}),
    ("Tem desconto no PIX?", {3}),
    ("Em quantas vezes posso parcelar no cartão?", {3}),
    ("Me arrependi da compra, posso devolver?", {4}),
    ("Meu produto veio com defeito, posso trocar?", {4}),
    ("Qual o prazo de entrega para outra cidade pelo SEDEX?", {5}),
    ("Promoções são cumulativas?", {6}),
    ("Quanto tempo dura a garantia?", {7}),
    ("Como peço para excluir meus dados pessoais?", {7, 8}),
])
def test_recuperacao_traz_pagina_certa_em_primeiro(pergunta, paginas):
    assert rag.retrieve(pergunta)[0]["page"] in paginas


@precisa_servidor
def test_pergunta_entre_secoes_traz_as_duas_paginas():
    # A resposta exige o desconto do PIX (p.3) e a regra de não acumular (p.6);
    # o agente recebe todos os trechos do top-k, então a ordem entre as duas não importa.
    paginas = {hit["page"] for hit in rag.retrieve("Promoção acumula com desconto do PIX?")}
    assert {3, 6} <= paginas
