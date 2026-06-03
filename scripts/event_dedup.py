"""
event_dedup.py — NLP-based event deduplication for trump-alert.

Problem
-------
Trump says something once (e.g. "go out and buy Dell" on May 8).
News outlets keep republishing the same statement for days or weeks with fresh
article dates, which pass our date filter and inflate the mention count.

Solution
--------
Semantic similarity: two articles about the same company whose text means the
same thing (regardless of paraphrasing across outlets) are treated as the same
event. Only the oldest report is kept; all re-quotes are dropped.

Two-layer approach (automatic fallback):
  Primary:  sentence-transformers all-MiniLM-L6-v2  (semantic, handles paraphrasing)
  Fallback: TF-IDF cosine similarity via scikit-learn (lexical, no model download)
  Last:     Jaccard word-overlap (stdlib only — always available)

Integration
-----------
Called from run_daily.py immediately before save_cache(), on the merged list of
all posts (cached + fresh).
"""

import os
import sys

# ---------------------------------------------------------------------------
# Sources that are NEVER deduplicated — they are primary Trump statements,
# not press re-tellings of those statements.
# ---------------------------------------------------------------------------
PRIMARY_SOURCES = {"truthsocial", "whitehouse_speech"}

# ---------------------------------------------------------------------------
# Similarity thresholds per backend.
# ST handles paraphrasing so a high threshold is safe.
# TF-IDF is purely lexical so we relax it slightly.
# ---------------------------------------------------------------------------
_THRESHOLD_ST      = 0.82
_THRESHOLD_TFIDF   = 0.62
_THRESHOLD_JACCARD = 0.40

# ---------------------------------------------------------------------------
# Backend state (lazy-initialised on first call)
# ---------------------------------------------------------------------------
_model   = None   # SentenceTransformer instance, or None
_backend = None   # "st" | "tfidf" | "jaccard"


def _init_backend() -> None:
    """Detect and load the best available similarity backend once."""
    global _model, _backend
    if _backend is not None:
        return

    # sentence-transformers is skipped when running in GitHub Actions CI
    # to avoid the HuggingFace model download on uncached runs.
    # Set TRUMP_ALERT_USE_ST=1 to force it even in CI.
    in_ci = os.getenv("GITHUB_ACTIONS") == "true"
    force_st = os.getenv("TRUMP_ALERT_USE_ST") == "1"

    if not in_ci or force_st:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _model = SentenceTransformer("all-MiniLM-L6-v2")
            _backend = "st"
            print("[dedup] backend=sentence-transformers (semantic)", file=sys.stderr)
            return
        except ImportError:
            pass
        except Exception as exc:
            print(f"[dedup] sentence-transformers failed ({exc}) — trying TF-IDF", file=sys.stderr)

    try:
        import sklearn  # noqa: F401
        _backend = "tfidf"
        print("[dedup] backend=TF-IDF / scikit-learn (lexical)", file=sys.stderr)
        return
    except ImportError:
        pass

    _backend = "jaccard"
    print("[dedup] backend=Jaccard word-overlap (stdlib)", file=sys.stderr)


def _threshold() -> float:
    return {"st": _THRESHOLD_ST, "tfidf": _THRESHOLD_TFIDF, "jaccard": _THRESHOLD_JACCARD}.get(
        _backend or "jaccard", _THRESHOLD_JACCARD
    )


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _post_text(post: dict) -> str:
    """
    Build a compact comparison string from a post.
    Uses the article title + mention context snippets (250 chars max).
    The title carries the event framing; snippets carry the quote/signal.
    """
    title = post.get("title", "")
    snippets = [
        m.get("context_snippet", "")
        for m in post.get("mentions", [])
        if m.get("context_snippet")
    ]
    body = " ".join(snippets)[:250]
    return f"{title} {body}".strip().lower()


def _shares_ticker(post_a: dict, post_b: dict) -> bool:
    """True if both posts mention at least one ticker in common."""
    ta = {m.get("ticker") for m in post_a.get("mentions", [])}
    tb = {m.get("ticker") for m in post_b.get("mentions", [])}
    return bool(ta & tb)


# ---------------------------------------------------------------------------
# Embedding + similarity
# ---------------------------------------------------------------------------

def _embed(text: str):
    """Return a normalised numpy embedding, or None for non-ST backends."""
    if _backend == "st" and _model is not None:
        return _model.encode(text, normalize_embeddings=True)
    return None


def _similarity(text_a: str, text_b: str, emb_a=None, emb_b=None) -> float:
    """
    Compute similarity between two texts using the active backend.

    ST backend:     cosine similarity on pre-computed unit-norm embeddings
                    (dot product == cosine when both vectors are normalised).
    TF-IDF backend: cosine similarity on TF-IDF sparse vectors.
    Jaccard:        |intersection| / |union| over word sets.
    """
    # ── Sentence-transformers ──────────────────────────────────────────────
    if _backend == "st" and emb_a is not None and emb_b is not None:
        try:
            import numpy as np
            return float(np.dot(emb_a, emb_b))
        except Exception:
            pass  # fall through

    # ── TF-IDF cosine ─────────────────────────────────────────────────────
    if _backend in ("st", "tfidf"):
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            vec = TfidfVectorizer(min_df=1, sublinear_tf=True).fit_transform([text_a, text_b])
            return float(cosine_similarity(vec[0:1], vec[1:2])[0][0])
        except Exception:
            pass  # fall through to Jaccard

    # ── Jaccard word-overlap ───────────────────────────────────────────────
    sa = set(text_a.split())
    sb = set(text_b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ---------------------------------------------------------------------------
# Main deduplication function
# ---------------------------------------------------------------------------

def dedup_posts(posts: list, verbose: bool = True) -> list:
    """
    Remove news articles that re-quote the same Trump event.

    Algorithm (greedy, stable):
      1. Primary-source posts (Truth Social, WH transcripts) are always kept.
      2. Secondary posts are sorted oldest-first so the original report
         is preferred over later re-quotes.
      3. For each secondary post, compare against all already-kept secondary
         posts that share at least one ticker.
      4. If max similarity ≥ threshold → drop as re-quote of known event.
      5. Otherwise → keep and add to the comparison pool.

    Parameters
    ----------
    posts   : merged list of all posts (cached + fresh)
    verbose : print dropped-article log lines to stderr

    Returns
    -------
    Deduplicated list: all primary posts + kept secondary posts.
    """
    _init_backend()

    primary   = [p for p in posts if p.get("source") in PRIMARY_SOURCES]
    secondary = [p for p in posts if p.get("source") not in PRIMARY_SOURCES]

    if not secondary:
        return posts  # nothing to dedup

    # Oldest article first — original reporting beats re-quotes
    secondary_sorted = sorted(secondary, key=lambda p: p.get("created_at", ""))

    kept: list[tuple[dict, str, object]] = []  # (post, text, embedding)
    dropped = 0
    thresh  = _threshold()

    for post in secondary_sorted:
        text = _post_text(post)
        emb  = _embed(text)

        is_dup = False
        for kept_post, kept_text, kept_emb in kept:
            if not _shares_ticker(post, kept_post):
                continue  # different companies — skip comparison

            sim = _similarity(text, kept_text, emb, kept_emb)
            if sim >= thresh:
                if verbose:
                    title_short = post.get("title", "")[:70]
                    date_short  = post.get("created_at", "")[:10]
                    print(
                        f"[dedup] drop sim={sim:.2f} ({_backend}) [{date_short}] {title_short}",
                        file=sys.stderr,
                    )
                is_dup = True
                dropped += 1
                break

        if not is_dup:
            kept.append((post, text, emb))

    kept_posts = [p for p, _, _ in kept]

    if dropped and verbose:
        print(
            f"[dedup] {dropped} duplicate article(s) removed  "
            f"({len(secondary_sorted)} → {len(kept_posts)} secondary posts, "
            f"threshold={thresh}, backend={_backend})",
            file=sys.stderr,
        )

    return primary + kept_posts


# ---------------------------------------------------------------------------
# CLI — run standalone to inspect dedup on the current cache
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    from pathlib import Path

    cache_path = Path.home() / "Documents" / "TrumpAlerts" / "post_cache.json"
    if not cache_path.exists():
        print("No cache found at", cache_path)
        sys.exit(1)

    data  = json.loads(cache_path.read_text(encoding="utf-8"))
    posts = data.get("posts", [])
    print(f"Cache has {len(posts)} posts before dedup.")
    deduped = dedup_posts(posts, verbose=True)
    print(f"After dedup: {len(deduped)} posts ({len(posts) - len(deduped)} removed).")
