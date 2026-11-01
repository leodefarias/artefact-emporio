"""Tests the ReAct loop logic with a fake LLM; needs no server."""
from datetime import datetime
from types import SimpleNamespace as NS

import agent


def _msg(content="", chamadas=()):
    tool_calls = [
        NS(id=f"c{i}", function=NS(name=nome, arguments=args)) for i, (nome, args) in enumerate(chamadas)
    ]
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls or None))], usage=NS(completion_tokens=1))


def _llm_roteirizado(monkeypatch, *respostas):
    fila, recebidas = list(respostas), []

    def create(**kwargs):
        recebidas.append([dict(m) for m in kwargs["messages"]])
        return fila.pop(0) if len(fila) > 1 else fila[0]

    monkeypatch.setattr(agent.client.chat.completions, "create", create)
    return recebidas


def test_ciclo_chama_ferramenta_e_devolve_observacao(monkeypatch):
    recebidas = _llm_roteirizado(
        monkeypatch, _msg(chamadas=[("buscar_produtos", '{"termo": "GD20"}')]), _msg("Sai por R$ 2.199,00")
    )
    resposta, usadas, _ = agent.responder([], "Quanto custa?")
    assert resposta == "Sai por R$ 2.199,00" and usadas == ["buscar_produtos"]
    assert "2.199,00" in recebidas[1][-1]["content"] and recebidas[1][-1]["role"] == "tool"


def test_guarda_recusa_preco_sem_consulta(monkeypatch):
    recebidas = _llm_roteirizado(
        monkeypatch,
        _msg("Custa R$ 1,00"),
        _msg(chamadas=[("buscar_produtos", '{"termo": "GD20"}')]),
        _msg("Custa R$ 2.199,00"),
    )
    resposta, usadas, _ = agent.responder([], "Quanto custa?")
    assert resposta == "Custa R$ 2.199,00" and usadas == ["buscar_produtos"]
    assert recebidas[1][-1]["content"] == agent.PEDE_CONSULTA


def test_loop_infinito_termina_em_mensagem_de_falha(monkeypatch):
    recebidas = _llm_roteirizado(monkeypatch, _msg(chamadas=[("buscar_produtos", "{}")]))
    resposta, _, _ = agent.responder([], "oi")
    assert resposta == agent.FALHA and len(recebidas) == agent.MAX_PASSOS


def test_argumento_invalido_vira_observacao_de_erro(monkeypatch):
    recebidas = _llm_roteirizado(monkeypatch, _msg(chamadas=[("buscar_produtos", "{quebrado")]), _msg("Pode repetir?"))
    resposta, _, _ = agent.responder([], "oi")
    assert resposta == "Pode repetir?" and "erro" in recebidas[1][-1]["content"]


def test_historico_guarda_so_conversa_e_e_limitado(monkeypatch):
    recebidas = _llm_roteirizado(monkeypatch, _msg(chamadas=[("buscar_produtos", '{"termo": "GD20"}')]), _msg("Certo"))
    historico = []
    for i in range(8):
        agent.responder(historico, f"m{i}")
    assert {m["role"] for m in historico} == {"user", "assistant"}
    assert len(recebidas[-1]) <= agent.MAX_HISTORICO + 2 + 2  # system, turn, call, and observation


def test_raciocinio_nao_vaza_para_o_cliente(monkeypatch):
    _llm_roteirizado(monkeypatch, _msg("<think>segredo</think>Olá!"))
    assert agent.responder([], "oi")[0] == "Olá!"


def test_pensamento_vai_para_o_trace_e_nao_para_a_ferramenta_nem_ao_cliente(monkeypatch):
    recebidas = _llm_roteirizado(
        monkeypatch,
        _msg(chamadas=[("buscar_produtos", '{"pensamento": "preciso do preço", "termo": "GD20"}')]),
        _msg("Sai por R$ 2.199,00"),
    )
    linhas = []
    resposta, _, _ = agent.responder([], "Quanto custa?", trace=linhas.append)
    assert "Pensamento: preciso do preço" in linhas
    assert "2.199,00" in recebidas[1][-1]["content"]  # did not become an argument error
    assert "preciso do preço" not in resposta


def test_toda_ferramenta_exige_pensamento():
    assert all("pensamento" in t["function"]["parameters"]["required"] for t in agent.TOOLS)


def test_resposta_sem_choices_vira_mensagem_de_falha(monkeypatch):
    _llm_roteirizado(monkeypatch, NS(choices=None, usage=None))
    assert agent.responder([], "oi")[0] == agent.FALHA


def test_expediente():
    assert "store open" in agent.contexto_atual(datetime(2026, 11, 2, 9, 0))    # Monday 9:00
    assert "store closed" in agent.contexto_atual(datetime(2026, 11, 2, 18, 0))  # Monday 18:00
    assert "store open" in agent.contexto_atual(datetime(2026, 10, 31, 12, 59))  # Saturday
    assert "store closed" in agent.contexto_atual(datetime(2026, 10, 31, 13, 0))
    assert "store closed" in agent.contexto_atual(datetime(2026, 11, 1, 10, 0))  # Sunday


def test_retorno_da_equipe():
    assert "staff returns Monday" in agent.contexto_atual(datetime(2026, 10, 31, 17, 40))  # Saturday afternoon
    assert "staff returns Monday" in agent.contexto_atual(datetime(2026, 11, 1, 8, 0))     # early Sunday
    assert "staff returns Tuesday" in agent.contexto_atual(datetime(2026, 11, 2, 19, 0))      # Monday night
    assert "staff returns Monday" in agent.contexto_atual(datetime(2026, 11, 2, 7, 0))     # early Monday
    assert "staff returns Saturday" in agent.contexto_atual(datetime(2026, 11, 6, 20, 0))           # Friday night
