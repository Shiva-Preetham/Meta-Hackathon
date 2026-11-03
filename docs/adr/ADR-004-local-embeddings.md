# ADR-004: Local embeddings

**Status:** Accepted

## Context
Clustering needs a vector per item. There is no verified Meta embeddings endpoint, and an
embedding call per item would add cost, latency and a network dependency.

## Decision
- Embed locally. Preferred model: `sentence-transformers/all-MiniLM-L6-v2` (384 dims),
  optional dependency (`requirements-embeddings.txt`, pulls in PyTorch).
- Fallback: `HashingEmbedder`, a deterministic bag of words (light suffix stemming, hashed
  unigrams + bigrams into 2,048 dims, log term frequency, L2 norm). Needs only
  scikit-learn, no download, no fitting, so it works online (new items never change old
  vectors).
- `SIGNAL_EMBEDDER=auto` uses MiniLM when it loads and hashing otherwise; the model id is
  stored with every vector.
- The embedded text is the key quote, the full text and the normalised product area as an
  anchor token (ADR-005 explains the anchor and its measured effect).

## Consequences
- Zero marginal cost and no network for embeddings; deterministic vectors.
- The hashing embedder does not understand paraphrase ("app won't open" vs "crashes on
  launch"). The product-area anchor from Spark's enrichment compensates for much of that.
- In the build environment Hugging Face was unreachable, so **all measured clustering
  numbers in this repo use the hashing embedder**. MiniLM thresholds are starting values,
  not measurements (see "Needs you" in PROGRESS.md).
