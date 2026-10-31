"""Ferramentas de dados do agente. Todo preço e toda data são calculados aqui, nunca pelo LLM."""
import csv
import json
import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from rag import buscar_politicas

DATA = Path(__file__).parent / "data"
LIMITE_RESULTADOS = 15
DESCONTO_PIX = 5  # manual §3
ACESSO_NEGADO = {
    "erro": "Não localizei pedido com esses dados. Confira o número do pedido "
    "e o e-mail ou telefone do cadastro."
}
STATUS_PEDIDO = {
    "pending": "pendente",
    "confirmed": "confirmado",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
}


def _ler(nome):
    with open(DATA / f"{nome}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


PRODUTOS = _ler("products")
CATEGORIAS = {c["category_id"]: c["name"] for c in _ler("categories")}
CLIENTES = {c["customer_id"]: c for c in _ler("customers")}
PEDIDOS = {p["order_id"]: p for p in _ler("orders")}
ITENS = _ler("order_items")
PROMOCOES_ATIVAS = {}
for _p in _ler("promotions"):
    if _p["is_active"] == "1":
        # manual §6.2: promoções não acumulam; vale a maior
        atual = PROMOCOES_ATIVAS.get(_p["product_id"])
        if not atual or int(_p["discount_percent"]) > int(atual["discount_percent"]):
            PROMOCOES_ATIVAS[_p["product_id"]] = _p


def _compacto(texto):
    """Minúsculas, sem acento e sem pontuação: 'GD-20' casa com 'gd20'."""
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sem_acento.lower())


def _brl(valor):
    q = Decimal(valor).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return "R$ " + f"{q:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _com_desconto(preco, percentual):
    return Decimal(preco) * (100 - percentual) / 100


def _situacao(produto):
    if produto["status"] == "discontinued":
        return "descontinuado"
    if produto["status"] == "coming_soon":
        return "em breve"
    return "disponível" if int(produto["stock_quantity"]) > 0 else "esgotado"


def _ficha(produto, detalhada):
    situacao = _situacao(produto)
    ficha = {
        "nome": produto["name"],
        "categoria": CATEGORIAS[produto["category_id"]],
        "situacao": situacao,
    }
    if situacao == "disponível":
        ficha["estoque"] = int(produto["stock_quantity"])
    if situacao != "descontinuado":  # fora do catálogo não tem preço a oferecer
        promocao = PROMOCOES_ATIVAS.get(produto["product_id"])
        if promocao:
            percentual = int(promocao["discount_percent"])
            ficha |= {
                "promocao": promocao["description"],
                "desconto": f"{percentual}%",
                "preco_original": _brl(produto["price_brl"]),
                "preco_promocional": _brl(_com_desconto(produto["price_brl"], percentual)),
            }
        else:
            ficha |= {
                "preco_tabela": _brl(produto["price_brl"]),
                "preco_pix": _brl(_com_desconto(produto["price_brl"], DESCONTO_PIX)),
            }
    ficha["descricao"] = produto["description"]  # base para recomendar ("bom para iniciante?")
    if detalhada:
        ficha["especificacoes"] = produto["specs"]
    return ficha


def buscar_produtos(termo=None, categoria=None, preco_max=None, so_disponiveis=None, so_promocao=False):
    # O LLM pode mandar "" ou "1000" em vez de null e 1000.
    if preco_max not in (None, ""):
        try:
            preco_max = Decimal(str(preco_max))
        except ArithmeticError:
            return {"erro": f"preco_max inválido: {preco_max!r}"}
    else:
        preco_max = None
    if categoria and categoria not in CATEGORIAS.values():
        return {"erro": f"Categoria inválida. Opções: {sorted(CATEGORIAS.values())}"}
    # Busca por nome mostra também esgotados, para o agente sugerir alternativas (manual §7.3).
    if so_disponiveis is None:
        so_disponiveis = not termo

    tokens = [_compacto(t) for t in (termo or "").split()]
    achados = []
    for p in PRODUTOS:
        categoria_p = CATEGORIAS[p["category_id"]]
        # ponytail: substring sobre o texto todo; trocar por busca semântica se o catálogo crescer
        texto = _compacto(f'{p["name"]} {p["description"]} {categoria_p}')
        if not all(t in texto for t in tokens):
            continue
        if categoria and categoria_p != categoria:
            continue
        if preco_max is not None and Decimal(p["price_brl"]) > preco_max:
            continue
        if so_disponiveis and _situacao(p) != "disponível":
            continue
        if so_promocao and p["product_id"] not in PROMOCOES_ATIVAS:
            continue
        achados.append(p)

    # Modelos pequenos erram a categoria (Takamine em "Guitarras"), e dizer que um produto
    # existente não existe é o pior erro possível: o nome vale mais que a categoria.
    if not achados and termo and categoria:
        return buscar_produtos(termo, None, preco_max, so_disponiveis, so_promocao)

    achados.sort(key=lambda p: Decimal(p["price_brl"]))
    detalhada = len(achados) <= 5
    return {
        "total": len(achados),
        "produtos": [_ficha(p, detalhada) for p in achados[:LIMITE_RESULTADOS]],
    }


def _contato_confere(cliente, contato):
    contato = str(contato).strip()
    if "@" in contato:
        return contato.casefold() == cliente["email"].casefold()
    digitos = re.sub(r"\D", "", contato)
    if len(digitos) > 11 and digitos.startswith("55"):
        digitos = digitos[2:]
    return bool(digitos) and digitos == re.sub(r"\D", "", cliente["phone"])


def consultar_pedido(order_id, email_ou_telefone, hoje=None):
    hoje = hoje or date.today()
    # A verificação fica aqui, e não no prompt, para o LLM não ter como contorná-la (LGPD).
    # Mesma resposta para pedido inexistente e contato errado: não revela quais pedidos existem.
    pedido = PEDIDOS.get(str(order_id).strip().lstrip("#"))
    if not pedido or not _contato_confere(CLIENTES[pedido["customer_id"]], email_ou_telefone):
        return ACESSO_NEGADO

    produtos = {p["product_id"]: p for p in PRODUTOS}
    previsao = pedido["estimated_delivery"]
    aberto = pedido["status"] in ("pending", "confirmed", "shipped")
    resposta = {
        "pedido": int(pedido["order_id"]),
        "cliente": CLIENTES[pedido["customer_id"]]["name"].split()[0],
        "status": STATUS_PEDIDO[pedido["status"]],
        "data_compra": pedido["order_date"],
        "dias_desde_compra": (hoje - date.fromisoformat(pedido["order_date"])).days,
        "total": _brl(pedido["total_brl"]),
        "pagamento": pedido["payment_method"],
        "itens": [
            {"nome": produtos[i["product_id"]]["name"], "quantidade": int(i["quantity"])}
            for i in ITENS
            if i["order_id"] == pedido["order_id"]
        ],
        "entrega_atrasada": bool(previsao) and aberto and date.fromisoformat(previsao) < hoje,
    }
    if pedido["tracking_code"]:
        resposta["rastreio"] = pedido["tracking_code"]
    if previsao:
        resposta["previsao_entrega"] = previsao
        # Os dados não têm data de recebimento; a previsão é a melhor aproximação (ver README).
        resposta["dias_desde_previsao_entrega"] = (hoje - date.fromisoformat(previsao)).days
    if pedido["notes"]:
        resposta["observacoes"] = pedido["notes"]
    return resposta


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_produtos",
            "description": "Consulta o catálogo: preço, estoque, disponibilidade, promoções ativas "
            "e características de instrumentos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo": {"type": "string", "description": "Nome, marca ou modelo. Ex.: 'Takamine GD20'."},
                    "categoria": {"type": "string", "enum": sorted(CATEGORIAS.values())},
                    "preco_max": {"type": "number", "description": "Preço máximo em reais."},
                    "so_disponiveis": {
                        "type": "boolean",
                        "description": "Só itens à venda com estoque. Se omitido: sim, exceto em busca por termo.",
                    },
                    "so_promocao": {"type": "boolean", "description": "Só itens com promoção ativa."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_pedido",
            "description": "Consulta status, itens, rastreio e prazos de um pedido. Exige o número do "
            "pedido E o e-mail ou telefone informados pelo cliente; se faltar um, peça antes de chamar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "integer"},
                    "email_ou_telefone": {"type": "string"},
                },
                "required": ["order_id", "email_ou_telefone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_politicas",
            "description": "Busca no manual da loja: horário, endereço, contato, o que a loja vende, formas "
            "de pagamento, parcelamento, trocas, devoluções, frete, garantia, regras de promoção e privacidade.",
            "parameters": {
                "type": "object",
                "properties": {"pergunta": {"type": "string"}},
                "required": ["pergunta"],
            },
        },
    },
]

# Modelos com tool calling costumam agir sem escrever texto. Um argumento obrigatório torna
# explícito o raciocínio de cada passo do ReAct, em qualquer modelo; o cliente nunca o vê.
for _tool in TOOLS:
    _parametros = _tool["function"]["parameters"]
    _parametros["properties"] = {
        "pensamento": {
            "type": "string",
            "description": "Uma frase: o que o cliente precisa, o que falta saber e por que esta ferramenta.",
        },
        **_parametros["properties"],
    }
    _parametros["required"] = ["pensamento", *_parametros.get("required", [])]


def executar(nome, argumentos):
    """Roda a ferramenta pedida pelo LLM. Argumento ruim volta como erro para ele corrigir."""
    funcoes = {
        "buscar_produtos": buscar_produtos,
        "consultar_pedido": consultar_pedido,
        "buscar_politicas": buscar_politicas,
    }
    if nome not in funcoes:
        return json.dumps({"erro": f"Ferramenta desconhecida: {nome}"}, ensure_ascii=False)
    try:
        resultado = funcoes[nome](**argumentos)
    except (TypeError, ArithmeticError) as e:
        return json.dumps({"erro": f"Argumentos inválidos: {e}"}, ensure_ascii=False)
    return resultado if isinstance(resultado, str) else json.dumps(resultado, ensure_ascii=False)
