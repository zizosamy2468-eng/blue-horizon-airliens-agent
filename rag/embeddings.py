# CHUNKING + EMBEDDING PIPELINE.
#
# Chunking: each PolicySection in policy_corpus.py is already a natural,
# self-contained chunk (one policy rule, a few sentences). We embed
# title + text together per section rather than splitting further --
# splitting IROPS-DUTY-3 mid-sentence would separate the override
# condition from the logging requirement that makes it meaningful.
#
# Embedding: real Google Gemini embeddings via Google AI Studio (free-tier
# API key). Uses the NEW unified google-genai SDK -- NOT the old
# google-generativeai package, which Google has since deprecated (that's
# the same deprecation issue this project already ran into once before
# with Gemini, per the project history -- using the current SDK here on
# purpose to avoid repeating it).
#
# Setup:
#   pip install google-genai python-dotenv
#   get a free API key at https://aistudio.google.com/apikey
#   add GOOGLE_API_KEY=... to your .env file (same file as OPENAI_API_KEY
#   would have gone, and DB_* -- see dbase.py for the pattern)

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from policy_corpus import PolicySection, get_manual

load_dotenv()

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768  # gemini-embedding-001 defaults to 3072 dims; 768 is plenty for a 17-chunk manual and cheaper to store

_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embeds a batch of texts in one API call. Returns one vector per input text, in order."""
    response = _client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIM),
    )
    return [e.values for e in response.embeddings]


def embed_one(text: str) -> list[float]:
    return embed_texts([text])[0]


def build_corpus_texts(sections: list[PolicySection] | None = None) -> tuple[list[PolicySection], list[str]]:
    """Returns (sections, texts) ready to embed and hand to vector_store.py."""
    sections = sections or get_manual()
    texts = [f"{s.title}. {s.text}" for s in sections]
    return sections, texts


if __name__ == "__main__":
    sections, texts = build_corpus_texts()
    vectors = embed_texts(texts)
    print(f"Embedded {len(sections)} policy chunks, each vector has {len(vectors[0])} dimensions.")

    # Sanity check: embed a query and print raw cosine similarity against
    # the two chunks that should clearly be the closest match.
    import numpy as np
    query_vec = np.array(embed_one("what is the compensation auto-approve cap amount"))
    for section, vec in zip(sections, vectors):
        if section.code in ("IROPS-COMP-4.2b", "IROPS-COMP-6", "IROPS-CREW-2"):
            v = np.array(vec)
            sim = np.dot(query_vec, v) / (np.linalg.norm(query_vec) * np.linalg.norm(v))
            print(f"  similarity={sim:.3f}  [{section.code}] {section.title}")