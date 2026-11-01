"""The agent's data tools. Every price and date is computed here, never by the LLM."""
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
        # manual §6.2: promotions do not stack; the largest applies
        atual = PROMOCOES_ATIVAS.get(_p["product_id"])
        if not atual or int(_p["discount_percent"]) > int(atual["discount_percent"]):
            PROMOCOES_ATIVAS[_p["product_id"]] = _p


def _compacto(texto):
    """Lowercase, no accents, no punctuation: 'GD-20' matches 'gd20'."""
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
    if situacao != "descontinuado":  # no longer in the catalog, so no price to offer
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
    ficha["descricao"] = produto["description"]  # basis for recommendations ("good for a beginner?")
    if detalhada:
        ficha["especificacoes"] = produto["specs"]
    return ficha


def buscar_produtos(termo=None, categoria=None, preco_max=None, so_disponiveis=None, so_promocao=False):
    # The LLM may send "" or "1000" instead of null and 1000.
    if preco_max not in (None, ""):
        try:
            preco_max = Decimal(str(preco_max))
        except ArithmeticError:
            return {"erro": f"preco_max inválido: {preco_max!r}"}
    else:
        preco_max = None
    if categoria and categoria not in CATEGORIAS.values():
        return {"erro": f"Categoria inválida. Opções: {sorted(CATEGORIAS.values())}"}
    # A name search also shows out-of-stock items, so the agent can suggest alternatives (manual §7.3).
    if so_disponiveis is None:
        so_disponiveis = not termo

    tokens = [_compacto(t) for t in (termo or "").split()]
    achados = []
    for p in PRODUTOS:
        categoria_p = CATEGORIAS[p["category_id"]]
        # ponytail: substring over all text; switch to semantic search if the catalog grows
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

    # Small models get the category wrong (Takamine under "Guitarras"), and denying that an
    # existing product exists is the worst possible error: the name outranks the category.
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
    # The check lives here, not in the prompt, so the LLM cannot bypass it (LGPD).
    # Same response for a nonexistent order and a wrong contact: does not reveal which orders exist.
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
        # The data has no delivery-received date; the estimate is the best approximation (see README).
        resposta["dias_desde_previsao_entrega"] = (hoje - date.fromisoformat(previsao)).days
    if pedido["notes"]:
        resposta["observacoes"] = pedido["notes"]
    return resposta


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_produtos",
            "description": "Looks up the catalog: price, stock, availability, active promotions, "
            "and instrument features.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo": {"type": "string", "description": "Name, brand, or model. E.g., 'Takamine GD20'."},
                    "categoria": {"type": "string", "enum": sorted(CATEGORIAS.values())},
                    "preco_max": {"type": "number", "description": "Maximum price in Brazilian reais."},
                    "so_disponiveis": {
                        "type": "boolean",
                        "description": "Only items on sale with stock. If omitted: yes, except when searching by termo.",
                    },
                    "so_promocao": {"type": "boolean", "description": "Only items with an active promotion."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_pedido",
            "description": "Looks up an order's status, items, tracking, and deadlines. Requires the order "
            "number AND the email or phone given by the customer; if one is missing, ask before calling.",
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
            "description": "Searches the store manual: hours, address, contact, what the store sells, payment methods, "
            "installments, exchanges, returns, shipping, warranty, promotion rules, and privacy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pergunta": {"type": "string", "description": "The question, in Brazilian Portuguese (the manual's language)."},
                },
                "required": ["pergunta"],
            },
        },
    },
]

# Tool-calling models often act without writing text. A required argument makes the reasoning
# of each ReAct step explicit, on any model; the customer never sees it.
for _tool in TOOLS:
    _parametros = _tool["function"]["parameters"]
    _parametros["properties"] = {
        "pensamento": {
            "type": "string",
            "description": "One sentence in English: what the customer needs, what is missing, and why this tool.",
        },
        **_parametros["properties"],
    }
    _parametros["required"] = ["pensamento", *_parametros.get("required", [])]


def executar(nome, argumentos):
    """Runs the tool the LLM asked for. A bad argument returns as an error for it to correct."""
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
