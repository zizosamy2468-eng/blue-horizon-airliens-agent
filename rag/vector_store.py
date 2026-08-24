# VECTOR DATABASE ARCHITECTURE concern.
#
# Uses Chroma (the lab's own suggested resource: docs.trychroma.com) for
# local persistence. Chroma gives us all three required pieces out of the
# box, not a bare list of vectors:
#   1) A real ANN index -- Chroma builds an HNSW index (via hnswlib)
#      under the hood for every collection automatically.
#   2) A metadata payload store -- every chunk's category/code/
#      last_reviewed travels alongside its vector as Chroma metadata.
#   3) A metadata index used for pre-filtering -- Chroma's `where=` clause
#      on query() filters candidates before/during the similarity search,
#      not after the top-k is already decided.
#
# This file only calls embed_texts()/embed_one() from embeddings.py to get
# vectors -- it never talks to the OpenAI API directly, keeping the
# "embedding provider" and "vector storage" concerns in separate files.

from dataclasses import dataclass

import chromadb

from embeddings import build_corpus_texts, embed_one, embed_texts
from policy_corpus import PolicySection

COLLECTION_NAME = "irops_policy_manual"
PERSIST_DIR = "./chroma_db"  # created next to this file on first run

# Safety Incident Agent (Adel) filters by category="safety" when retrieving
# IROPS-SAFE-* sections. Rebuild the collection after policy_corpus changes
# so new safety sections are indexed (PolicyVectorStore(rebuild=True)).


@dataclass
class SearchResult:
    chunk_id: str          # policy code, e.g. "IROPS-COMP-4.2b"
    section: PolicySection
    score: float            # similarity (1 - cosine distance), higher is better


class PolicyVectorStore:
    def __init__(self, persist_dir: str = PERSIST_DIR, rebuild: bool = False):
        self.client = chromadb.PersistentClient(path=persist_dir)

        if rebuild:
            try:
                self.client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass  # didn't exist yet, nothing to delete

        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

        # Index the full policy manual only if the collection is empty --
        # re-embedding on every process start would waste API calls.
        if self.collection.count() == 0:
            self._build_index()

    def _build_index(self) -> None:
        sections, texts = build_corpus_texts()
        vectors = embed_texts(texts)

        self.collection.add(
            ids=[s.code for s in sections],
            embeddings=vectors,
            documents=[s.text for s in sections],
            metadatas=[
                {"category": s.category, "title": s.title, "last_reviewed": s.last_reviewed}
                for s in sections
            ],
        )
        self._sections_by_code = {s.code: s for s in sections}

    def search(self, query_text: str, k: int = 3, category: str | None = None) -> list[SearchResult]:
        query_vec = embed_one(query_text)

        # `where` is Chroma's metadata pre-filter -- when category is set,
        # only chunks matching it are candidates for the similarity search
        # at all, not filtered out of the results afterward.
        where = {"category": category} if category else None

        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=k,
            where=where,
        )

        out = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for chunk_id, doc, meta, distance in zip(ids, docs, metas, distances):
            section = PolicySection(
                code=chunk_id,
                category=meta["category"],
                title=meta["title"],
                text=doc,
                last_reviewed=meta["last_reviewed"],
            )
            similarity = 1 - distance  # Chroma returns cosine distance, not similarity
            out.append(SearchResult(chunk_id=chunk_id, section=section, score=similarity))

        return out


if __name__ == "__main__":
    # rebuild=True forces a fresh embed+index on this run; drop it on later
    # runs once you trust the collection is already built, to save API calls.
    store = PolicyVectorStore(rebuild=True)

    print("=== Unfiltered search: 'compensation cap amount' ===")
    for r in store.search("compensation cap amount", k=3):
        print(f"  {r.score:.3f}  [{r.section.code}] {r.section.title}")

    print("\n=== Filtered search (category='duty_time'): 'compensation cap amount' ===")
    print("(query mentions compensation, but the where= filter forces duty_time-only candidates)")
    for r in store.search("compensation cap amount", k=3, category="duty_time"):
        print(f"  {r.score:.3f}  [{r.section.code}] {r.section.title}")

    print("\n=== Unfiltered search: 'crew member exceeding duty hours override' ===")
    for r in store.search("crew member exceeding duty hours override", k=3):
        print(f"  {r.score:.3f}  [{r.section.code}] {r.section.title}")