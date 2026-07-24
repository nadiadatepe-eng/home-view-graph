#!/usr/bin/env python3
"""A static lookup-table embedder: semantic vectors without a neural net.

Borrowed idea (idea harvest 2026-07-24, codegraph-ai/CodeGraph, the model2vec
pattern): an embedding does not need a transformer at inference. Distil a
teacher model ONCE, offline, into a `vocab x dim` matrix, ship the matrix as a
data file, and at run time an embedding is pure arithmetic:

    tokenise -> look each token's row up in the matrix -> weighted mean-pool
             -> L2-normalise

No ONNX, no torch, no download, no `dependencies = []` broken. codegraph-ai
measured static+BM25 at roughly 90-94% of a heavy BGE model's quality at ~100x
the speed, which is the trade this project wants: most of the recall, none of
the runtime weight.

**The distillation is a build-time script, not this file.** Producing the matrix
needs a teacher transformer and a network; that happens on someone's machine,
once, and its output -- the data file -- is what ships. Nothing here downloads
or trains. `load()` reads a file and refuses if it is absent; it never reaches
for a default off the internet, because a provider that silently fetches a model
is the code-review-graph #711 failure this whole package is built to avoid.

**The matrix that gates run against is SYNTHETIC.** Real distilled vectors are
not reproducible across machines -- a different teacher build, a different BLAS,
and the floats differ in the last places -- so a test that embedded real text
would be asserting against numbers no other machine computes. The checkpoints
use a tiny hand-authored matrix with known vectors instead (a "fake embedder"),
which makes the arithmetic deterministic and the ranking claims checkable. The
real matrix plugs into the same loader later; the mechanism does not change.

Identifier-splitting is the one non-obvious tokenizer rule (codegraph-ai #4,
+6% recall): `getUserById` and `get_user_by_id` and `get-user-by-id` all become
`get user by id`, so a query written one way finds text written another. Code
and note titles are full of camelCase and snake_case, and a tokenizer that kept
them whole would put every identifier in a vocabulary of one.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

# Split on anything that is not a letter or digit; then break camel/Pascal
# humps. Order matters: the acronym rule runs before the lower->upper rule so
# that `HTMLParser` becomes `HTML Parser` and not `HTM LParser`.
_NONWORD = re.compile(r"[^0-9A-Za-z]+")
_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")     # HTMLParser -> HTML Parser
_HUMP = re.compile(r"([a-z0-9])([A-Z])")           # getUser    -> get User


class EmbedderDataMissing(FileNotFoundError):
    """The data file named by the config does not exist.

    A distinct type so the CLI can exit 2 with an instruction rather than dump a
    traceback: a missing matrix is a setup step not yet taken, not a crash.
    """


class StaticEmbedError(RuntimeError):
    """The data file exists but is not a usable matrix."""


def split_identifiers(text: str) -> list[str]:
    """`text` -> lower-case tokens, with identifiers split into their words.

    Pure and deterministic: the same string yields the same list on any
    machine, which is what lets the synthetic-matrix gates assert exact vectors.
    """
    spaced = _NONWORD.sub(" ", text)
    spaced = _ACRONYM.sub(r"\1 \2", spaced)
    spaced = _HUMP.sub(r"\1 \2", spaced)
    return [tok.lower() for tok in spaced.split()]


@dataclass
class Embedder:
    provider: str
    model: str
    dim: int
    # token -> its row in the matrix, and token -> pool weight. Weights are the
    # "weighted" in weighted mean-pool: a distiller emits per-token weights
    # (an idf-like down-weighting of ubiquitous tokens) so that "the" does not
    # drag every vector toward the same place. Absent -> every token weighs 1.0,
    # i.e. a plain mean, which is the honest default rather than a guessed idf.
    vectors: dict[str, list[float]]
    weights: dict[str, float]

    @property
    def namespace(self) -> tuple[str, str, int]:
        return (self.provider, self.model, self.dim)

    def embed(self, text: str) -> list[float]:
        """`text` -> an L2-normalised vector of length `dim`.

        Out-of-vocabulary tokens are skipped, not zero-filled: a token with no
        row contributes nothing rather than pulling the mean toward the origin.
        A string with NO in-vocabulary token returns the zero vector -- honest
        emptiness, and its cosine with anything is 0, so it ranks nothing rather
        than ranking everything equally by accident.
        """
        acc = [0.0] * self.dim
        total = 0.0
        for tok in split_identifiers(text):
            row = self.vectors.get(tok)
            if row is None:
                continue
            w = self.weights.get(tok, 1.0)
            for i in range(self.dim):
                acc[i] += w * row[i]
            total += w
        if total == 0.0:
            return acc                       # all zeros: no known token
        acc = [x / total for x in acc]       # mean-pool
        norm = math.sqrt(sum(x * x for x in acc))
        if norm == 0.0:
            return acc                       # tokens cancelled out exactly
        return [x / norm for x in acc]


def cosine(a: "Sequence[float]", b: "Sequence[float]") -> float:
    """Cosine similarity. `embed` returns unit vectors, so this is their dot.

    Written as the dot rather than a full cosine on purpose: both operands come
    from `embed`, both are already L2-normalised (or zero), and re-dividing by
    norms that are 1 would only invite a divide-by-zero on the zero vector. A
    zero operand yields 0.0, which is the correct "no evidence" answer.
    """
    return sum(x * y for x, y in zip(a, b))


def load(path: str) -> Embedder:
    """Read a matrix data file into an `Embedder`. Never touches the network.

    The format is a single JSON object -- provider, model, dim, a `tokens` list
    and a `matrix` of one row per token, plus an optional `weights` list. JSON
    because it is stdlib, diffable, and carries no code; a real 30k x 256 matrix
    is larger this way than a packed binary, but the loader's contract is the
    file, not the encoding, and a binary variant can replace it behind the same
    `load()` without the search path noticing.
    """
    try:
        with open(path, "rb") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise EmbedderDataMissing(
            "no embedding matrix at %s -- run the distillation script to build "
            "one, or point [embeddings].path at an existing file" % path
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StaticEmbedError(
            "embedding matrix at %s is unreadable: %s" % (path, exc)) from exc

    try:
        provider = str(raw["provider"])
        model = str(raw["model"])
        dim = int(raw["dim"])
        tokens = raw["tokens"]
        matrix = raw["matrix"]
    except (KeyError, TypeError, ValueError) as exc:
        raise StaticEmbedError(
            "%s is missing a required field (provider, model, dim, tokens, "
            "matrix): %s" % (path, exc)) from exc

    if not isinstance(tokens, list) or not isinstance(matrix, list):
        raise StaticEmbedError("%s: tokens and matrix must be lists" % path)
    if len(tokens) != len(matrix):
        raise StaticEmbedError(
            "%s: %d tokens but %d matrix rows -- the vocabulary and the matrix "
            "have drifted apart" % (path, len(tokens), len(matrix)))
    if dim <= 0:
        raise StaticEmbedError("%s: dim must be positive, got %d" % (path, dim))

    vectors: dict[str, list[float]] = {}
    for tok, row in zip(tokens, matrix):
        if not isinstance(row, list) or len(row) != dim:
            raise StaticEmbedError(
                "%s: token %r has a %s-length row, expected dim %d"
                % (path, tok, len(row) if isinstance(row, list) else "non-list",
                   dim))
        vectors[str(tok)] = [float(x) for x in row]

    weights: dict[str, float] = {}
    raw_weights = raw.get("weights")
    if raw_weights is not None:
        if not isinstance(raw_weights, list) or len(raw_weights) != len(tokens):
            raise StaticEmbedError(
                "%s: weights, if present, must be one per token (%d), got %s"
                % (path, len(tokens),
                   len(raw_weights) if isinstance(raw_weights, list) else "non-list"))
        weights = {str(tok): float(w) for tok, w in zip(tokens, raw_weights)}

    return Embedder(provider=provider, model=model, dim=dim,
                    vectors=vectors, weights=weights)


def from_config(cfg: dict[str, object]) -> Embedder:
    """Build the embedder a `[embeddings]` config asks for.

    Static only, for now: a network provider would open a socket the user named,
    and until one is wired an unknown provider is refused rather than guessed.

    The config's `model` and `dim` are checked against the DATA FILE's own,
    because the vectors are written and later filtered under the data file's
    namespace, not the config's. A config that says `model = "A"` while pointing
    at B's matrix would store vectors tagged B and then search for A, find zero,
    and degrade to lexical-only in silence -- the namespace mismatch made
    invisible. Caught here instead, loudly, at the one place both are in hand.
    """
    provider = cfg.get("provider")
    if provider != "static":
        raise StaticEmbedError(
            "embeddings provider %r is not supported; only 'static' is wired "
            "up (a network provider would need an explicit endpoint and is not "
            "built yet)" % (provider,))
    path = cfg.get("path")
    if not path or not isinstance(path, str):
        raise StaticEmbedError(
            "the static provider needs [embeddings].path pointing at a matrix "
            "data file")
    emb = load(path)

    declared_model = cfg.get("model")
    if declared_model is not None and str(declared_model) != emb.model:
        raise StaticEmbedError(
            "[embeddings].model is %r but the matrix at %s is model %r -- the "
            "vectors would be stored and searched under different names"
            % (declared_model, path, emb.model))
    # TOML parses `dim = 256` as an int; a `dim` that is present but not one is
    # itself a config mistake worth naming, rather than a check quietly skipped.
    declared_dim = cfg.get("dim")
    if declared_dim is not None:
        if not isinstance(declared_dim, int):
            raise StaticEmbedError(
                "[embeddings].dim must be an integer, got %r" % (declared_dim,))
        if declared_dim != emb.dim:
            raise StaticEmbedError(
                "[embeddings].dim is %d but the matrix at %s has dim %d"
                % (declared_dim, path, emb.dim))
    return emb
