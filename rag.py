"""RAG das políticas da loja: porte da célula 106 do notebook ia_e_machine_learning.ipynb.

Mesma receita (pdfplumber, chunks de 1200/150, vetores normalizados, índice em numpy,
top-k por produto escalar), trocando o embedding do Gemini por um servidor local.
Uso: python rag.py ingerir | python rag.py perguntar "sua pergunta"
"""
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pdfplumber
from openai import OpenAI

RAIZ = Path(__file__).parent
PDF = RAIZ / "data" / "politicas_da_loja.pdf"
INDEX_DIR = RAIZ / "index"
BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1234/v1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-bge-m3")

CHUNK_CHARS, OVERLAP, TOP_K, BATCH = 1200, 150, 4, 16

# O manual se contradiz; adotamos uma versão de cada dado (justificativa no README).
CORRECOES = {
    "3321-4500": "3341-4444",
    "contato@empóriodamusica": "contato@emporiodamusica",
    "Parcela mínima de R$ 100,00": "Parcela mínima conforme item 3.1",
}

client = OpenAI(base_url=BASE_URL, api_key="local")


# ---------------- ingest (offline) ----------------

def limpar_pagina(texto):
    """Tira cabeçalho e rodapé repetidos, que só poluem o embedding, e aplica as correções."""
    linhas = [
        linha for linha in texto.splitlines()
        if not linha.startswith("Empório da Música Manual de Políticas")
        and not re.fullmatch(r"Página \d+", linha.strip())
    ]
    limpo = "\n".join(linhas)
    for errado, certo in CORRECOES.items():
        limpo = limpo.replace(errado, certo)
    return limpo.strip()


def extract_pages(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pages.append((i, limpar_pagina(page.extract_text() or "")))
    return pages


def chunk_pages(pages):
    """Empacota o texto em pedaços de ~CHUNK_CHARS, em fronteira de palavra, com OVERLAP."""
    chunks = []
    for page_no, text in pages:
        words = text.split()
        current_chunk = []
        current_len = 0

        for word in words:
            word_len = len(word) + (1 if current_chunk else 0)

            if current_len + word_len > CHUNK_CHARS and current_chunk:
                chunks.append((page_no, " ".join(current_chunk)))

                overlap_words = []
                overlap_len = 0
                for w in reversed(current_chunk):
                    test_len = len(w) + (1 if overlap_words else 0)
                    if overlap_len + test_len <= OVERLAP:
                        overlap_words.insert(0, w)
                        overlap_len += test_len
                    else:
                        break

                # O notebook descartava `word` neste ponto, perdendo uma palavra a cada corte.
                current_chunk = overlap_words + [word]
                current_len = overlap_len + len(word) + (1 if overlap_words else 0)
            else:
                current_chunk.append(word)
                current_len += word_len

        if current_chunk:
            chunks.append((page_no, " ".join(current_chunk)))
    return chunks


def embed(texts):
    vecs = []
    for i in range(0, len(texts), BATCH):
        response = client.embeddings.create(model=EMBED_MODEL, input=texts[i : i + BATCH])
        vecs.extend(item.embedding for item in response.data)
    v = np.array(vecs, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)  # normalizado: produto escalar == cosseno


def ingest(pdf_path=PDF):
    pages = extract_pages(pdf_path)
    if sum(len(t) for _, t in pages) < 40:
        sys.exit("PDF sem texto extraível: provavelmente escaneado; rode OCR antes.")
    chunks = chunk_pages(pages)
    print(f"{len(pages)} páginas -> {len(chunks)} chunks. Gerando embeddings com {EMBED_MODEL} ...")
    index = embed([c[1] for c in chunks])
    INDEX_DIR.mkdir(exist_ok=True)
    np.save(INDEX_DIR / "index.npy", index)
    with open(INDEX_DIR / "chunks.json", "w", encoding="utf-8") as f:
        json.dump([{"page": p, "text": t} for p, t in chunks], f, ensure_ascii=False)
    load_index.cache_clear()
    print(f"Salvo {INDEX_DIR}/index.npy {index.shape} + chunks.json")


# ---------------- query (online) ----------------

@lru_cache(maxsize=1)  # o notebook relia o índice do disco a cada pergunta
def load_index():
    if not (INDEX_DIR / "index.npy").exists():
        ingest()
    index = np.load(INDEX_DIR / "index.npy")
    with open(INDEX_DIR / "chunks.json", encoding="utf-8") as f:
        return index, json.load(f)


def retrieve(question, k=TOP_K):
    index, chunks = load_index()
    q = embed([question])[0]
    hits = np.argsort(index @ q)[::-1][:k]
    return [{"page": chunks[i]["page"], "score": float(index[i] @ q),
             "text": chunks[i]["text"]} for i in hits]


def buscar_politicas(pergunta):
    """Trechos numerados, no formato do notebook, para o agente responder e citar."""
    hits = retrieve(pergunta)
    return "\n\n".join(f"[{n}] (p. {h['page']}) {h['text']}" for n, h in enumerate(hits, 1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ingerir":
        ingest()
    elif cmd == "perguntar" and len(sys.argv) > 2:
        for hit in retrieve(sys.argv[2]):
            print(f"p.{hit['page']} (score {hit['score']:.2f}): {hit['text'][:160]}...")
    else:
        sys.exit('Uso:\n  python rag.py ingerir\n  python rag.py perguntar "pergunta"')
