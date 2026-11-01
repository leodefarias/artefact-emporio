"""RAG over the store policies: a port of cell 106 of the ia_e_machine_learning.ipynb notebook.

Same recipe (pdfplumber, 1200/150 chunks, normalized vectors, numpy index, top-k by dot
product). Only access changes: an OpenAI-compatible API instead of the Gemini SDK.
Usage: python rag.py ingerir | python rag.py perguntar "your question"
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
BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "google/gemini-embedding-001")
# One folder per model: vectors from different models are not comparable.
INDEX_DIR = RAIZ / "index" / re.sub(r"\W+", "_", EMBED_MODEL)

CHUNK_CHARS, OVERLAP, TOP_K, BATCH = 1200, 150, 4, 16

# The manual contradicts itself; we adopt one version of each fact (rationale in the README).
CORRECOES = {
    "3321-4500": "3341-4444",
    "contato@empóriodamusica": "contato@emporiodamusica",
    "Parcela mínima de R$ 100,00": "Parcela mínima conforme item 3.1",
}

# Without a key, the first call fails with a 401, and the CLI explains what to configure.
client = OpenAI(base_url=BASE_URL, api_key=os.environ.get("LLM_API_KEY") or "sem-chave")


# ---------------- ingest (offline) ----------------

def limpar_pagina(texto):
    """Removes repeated headers and footers, which only pollute the embedding, and applies corrections."""
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
    """Packs text into ~CHUNK_CHARS pieces, on word boundaries, with OVERLAP."""
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

                # The notebook dropped `word` here, losing one word at each cut.
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
    return v / np.linalg.norm(v, axis=1, keepdims=True)  # normalized: dot product == cosine


def ingest(pdf_path=PDF):
    pages = extract_pages(pdf_path)
    if sum(len(t) for _, t in pages) < 40:
        sys.exit("PDF sem texto extraível: provavelmente escaneado; rode OCR antes.")
    chunks = chunk_pages(pages)
    print(f"{len(pages)} páginas -> {len(chunks)} chunks. Gerando embeddings com {EMBED_MODEL} ...", file=sys.stderr)
    index = embed([c[1] for c in chunks])
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(INDEX_DIR / "index.npy", index)
    with open(INDEX_DIR / "chunks.json", "w", encoding="utf-8") as f:
        json.dump([{"page": p, "text": t} for p, t in chunks], f, ensure_ascii=False)
    load_index.cache_clear()
    print(f"Salvo {INDEX_DIR}/index.npy {index.shape} + chunks.json", file=sys.stderr)


# ---------------- query (online) ----------------

@lru_cache(maxsize=1)  # the notebook reread the index from disk on every question
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
    """Numbered excerpts, in the notebook's format, for the agent to answer from and cite."""
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
