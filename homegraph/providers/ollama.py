#!/usr/bin/env python3
"""An Ollama-backed embedder: the same contract, over a socket the user named.

`static_embed` answers from a data file. This one answers from a local
inference server the user is already running. Both hand `search.vector_search`
the same two things -- a `namespace` and `embed(text) -> list[float]` -- so the
search path does not learn that a network exists. That was the promise in this
package's docstring, and this file is the second provider that keeps it.

**Nothing here is a default.** The endpoint is required, never guessed: a
provider that fell back to `localhost:11434` on its own would be reaching for a
network nobody named, which is the code-review-graph #711 failure the whole
package is designed against. `[embeddings]` absent means off; present with
`provider = "ollama"` and no `endpoint` is a refusal with the fix in it, not a
guess. (The locator form `ollama://model@host` may omit the PORT, because the
host is still explicitly named -- the standard 11434 is a convention about where
a named host listens, not a decision to go somewhere.)

**`dim` is learned, not declared.** The matrix file self-describes its dim; a
server can only be asked. So `connect()` embeds one short probe string and reads
the length of what comes back. That single call does double duty: it is also the
reachability check, so an unreachable endpoint fails at connect time -- before
any store is opened and before a single vector is written -- rather than halfway
through embedding six thousand nodes. If the config also declares a `dim`, it is
checked against the measured one, for the same reason `static_embed.from_config`
checks the declared model against the file's: vectors written under one
namespace and searched under another find nothing, silently, and look exactly
like "semantic search found no match".

**Vectors are L2-normalised here, and the reason is narrower than it first
looked.** Ollama's own documentation says `/api/embed` returns L2-normalised
vectors, so this is not covering for an absent guarantee -- an earlier draft of
this paragraph claimed it was, and that was wrong. It is covering for the fact
that the guarantee is not OURS: it belongs to a server the user chose, possibly
behind a proxy, possibly an older build, possibly something else entirely
speaking the same wire format. `search._cosine` is a plain dot product over
operands it is promised are unit vectors, and an un-normalised one would not
crash -- it would rank long vectors above close ones, silently. One pass over
384 floats removes a whole failure mode; keeping it is cheaper than trusting
someone else's invariant across versions we do not control.

**The gates never talk to a real Ollama.** Embeddings from a live model are not
reproducible across model versions, quantisations or machines, so a known-answer
test against one would assert numbers no other machine computes -- green only
where it was written. CP-I1 stands up a local `http.server` that returns fixed
vectors instead, which tests what this file is actually responsible for: the
request shape, the response parsing, the normalisation, the dim contract and the
error paths it can reach that way. It is not exhaustive over "every error path",
and saying so would be the kind of claim this project treats as a defect. The
model itself is Ollama's responsibility, not this file's.
"""
from __future__ import annotations

import http.client
import json
import math
import socket
import urllib.error
import urllib.request

# The current endpoint. Ollama also still serves the older `/api/embeddings`
# (singular, `prompt`/`embedding`), but that one is deprecated upstream and the
# two differ only in spelling; carrying both would be two code paths that agree
# until one of them is edited. One is enough, and it is the one with a future.
EMBED_PATH = "/api/embed"

# What `connect` embeds to learn the dim. Short, ASCII, and meaningless on
# purpose: it is measured for its LENGTH, never for its content, so nothing
# downstream can come to depend on what this string is.
PROBE_TEXT = "homegraph"

DEFAULT_PORT = 11434
DEFAULT_TIMEOUT = 30.0


class OllamaError(RuntimeError):
    """Any refusal from this provider. The CLI turns it into exit 2."""


class OllamaUnreachable(OllamaError):
    """The endpoint did not answer at all -- wrong host, port, or nothing running.

    Distinct from `OllamaModelMissing` because the fixes are different and a
    message that conflated them would send the user to `ollama pull` when the
    server is not running, or to `ollama serve` when the model is absent.
    """


class OllamaModelMissing(OllamaError):
    """The server answered, but does not have (or cannot embed with) that model.

    Ollama reports both "no such model" and "this model has no embedding
    capability" through the same error channel, and the instruction is nearly
    the same either way -- pull an embedding model -- so they share a type and
    the message carries whatever the server actually said.
    """


def _post(endpoint: str, path: str, payload: dict, timeout: float) -> dict:
    """POST JSON, read JSON back. The only place this package opens a socket.

    Failures are turned into this module's types, because the callers above are
    a CLI that must print an instruction and exit 2 -- a bare `URLError` reaching
    the top would be a traceback where a sentence belongs. Three families are
    caught: urllib's own (`HTTPError` before its superclass `URLError`), the
    OS-level ones, and `http.client.HTTPException`, which derives from
    `Exception` rather than `OSError` and therefore escaped the first cut
    entirely -- reproduced by pointing the locator at a TLS port, where the
    handshake bytes are read as a status line.
    """
    url = endpoint.rstrip("/") + path
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
    except (TypeError, ValueError) as exc:
        # Request construction can reject a URL urllib will not parse. Inside
        # the boundary, because this function's contract is that its callers
        # never see anything but this module's types.
        raise OllamaError(
            "cannot build a request for %s: %s" % (url, exc)) from exc
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # The server answered, with a status. Ollama puts a human-readable
        # reason in a JSON `error` field; surface it verbatim rather than
        # paraphrasing a message we did not write.
        try:
            detail = json.loads(exc.read()).get("error", "")
        except Exception:                                       # noqa: BLE001
            detail = ""
        finally:
            exc.close()          # HTTPError owns a connection; do not leak it
        raise _classify(url, exc.code, detail, payload) from exc
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        raise OllamaUnreachable(
            "no Ollama at %s (%s).\n\nStart one, or point [embeddings].endpoint "
            "at the right host. Nothing was written." % (url, exc)) from exc
    except http.client.HTTPException as exc:
        # BadStatusLine, IncompleteRead, InvalidURL and friends derive from
        # Exception, NOT from OSError, so the clause above does not see them.
        # They are reachable in ordinary use: point the locator at a TLS port
        # and the handshake bytes are read as a status line. Left uncaught they
        # were a traceback on plain command-line input.
        raise OllamaUnreachable(
            "%s did not speak HTTP (%s: %s).\n\nIs that really an Ollama "
            "endpoint? Nothing was written."
            % (url, type(exc).__name__, exc)) from exc

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OllamaError(
            "%s did not return JSON: %s" % (url, exc)) from exc
    if not isinstance(data, dict):
        raise OllamaError("%s returned %s, expected an object"
                          % (url, type(data).__name__))
    # Ollama documents errors as non-2xx with a JSON `error` field, so a 200
    # carrying one is not the documented contract -- but a proxy in front of it
    # can produce exactly that, and parsing an error body for a vector is the
    # worse failure. Classified by the same rules as a status-line error rather
    # than assumed to be about the model.
    if data.get("error"):
        raise _classify(url, 200, str(data["error"]), payload)
    return data


def _classify(url: str, code: int, detail: str, payload: dict) -> OllamaError:
    """Turn a server-reported failure into the type whose FIX is the right one.

    `OllamaUnreachable` and `OllamaModelMissing` exist because the actions
    differ -- start a server versus pull a model -- so a classifier that guesses
    wrong sends the reader to the wrong place. The first cut guessed on any
    status whose text merely contained "model" or "embed", which made a proxy's
    502 "model runner crashed" read as "pull an embedding model", and made every
    400 (including one caused by a request WE malformed) say the same.

    Narrow now: 404 is the documented shape for an absent model; a 400 counts
    only when the server's own text names the model. Everything else keeps the
    server's words and does not append an instruction we cannot justify.
    """
    said = detail.lower()
    model = payload.get("model")
    names_model = bool(model) and str(model).lower() in said
    if code == 404 or (code == 400 and (names_model or "model" in said)):
        return OllamaModelMissing(
            "%s answered HTTP %d for model %r: %s\n\nPull an embedding model "
            "(`ollama pull all-minilm` or `nomic-embed-text`); a "
            "completion-only model cannot embed."
            % (url, code, model, detail or "no detail"))
    if names_model or "embed" in said:
        return OllamaModelMissing(
            "%s answered HTTP %d for model %r: %s"
            % (url, code, model, detail or "no detail"))
    return OllamaError(
        "%s answered HTTP %d: %s" % (url, code, detail or "no detail"))


def _vectors_from(data: dict, url: str, want: int) -> list[list[float]]:
    """Pull exactly `want` vectors out of an `/api/embed` response.

    A response carrying a different count is refused rather than truncated or
    padded. With one input that is pedantry; with a batch it is the difference
    between a store that is wrong and a store that says so. Silently taking the
    first N would write vector *i* onto text *i+1* for every text after the gap
    -- right numbers, wrong nodes, and nothing anywhere reports an error.
    """
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != want:
        raise OllamaError(
            "%s returned %s embedding(s) for %d input(s); expected exactly %d"
            % (url, len(vectors) if isinstance(vectors, list) else "no",
               want, want))
    out = []
    for row in vectors:
        if not isinstance(row, list) or not row:
            raise OllamaError("%s returned an empty or non-list vector" % url)
        try:
            out.append([float(x) for x in row])
        except (TypeError, ValueError) as exc:
            raise OllamaError(
                "%s returned a vector with a non-numeric entry: %s" % (url, exc)
            ) from exc
    return out


def _vector_from(data: dict, url: str) -> list[float]:
    """Pull the one vector out of an `/api/embed` response.

    `/api/embed` is batch-shaped -- `embeddings` is a list of vectors, one per
    input -- and we send exactly one input, so exactly one is expected. A
    response carrying a different count is a contract break worth naming, not a
    list to take the first element of and hope.
    """
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != 1:
        raise OllamaError(
            "%s returned %s embedding(s) for one input; expected exactly 1"
            % (url, len(vectors) if isinstance(vectors, list) else "no"))
    row = vectors[0]
    if not isinstance(row, list) or not row:
        raise OllamaError("%s returned an empty or non-list vector" % url)
    try:
        return [float(x) for x in row]
    except (TypeError, ValueError) as exc:
        raise OllamaError(
            "%s returned a vector with a non-numeric entry: %s" % (url, exc)
        ) from exc


def l2_normalise(vec: list[float]) -> list[float]:
    """Scale to unit length; leave an all-zero vector alone.

    A zero vector stays zero rather than becoming NaN: its cosine with anything
    is 0.0, which is the honest "no evidence" answer that `static_embed` also
    returns for text of only out-of-vocabulary tokens. Dividing by a zero norm
    would turn that into NaN, and NaN sorts unpredictably -- a ranking bug that
    only shows up on the one input nobody tests.
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class OllamaEmbedder:
    """Satisfies `search.Embedder`: a namespace, and text -> unit vector.

    Not a dataclass, unlike `static_embed.Embedder`, and deliberately without
    `__eq__`. Two instances are NOT interchangeable just because they name the
    same endpoint -- they can carry different models, different measured dims
    and different timeouts -- and a generated equality over a socket
    configuration would invite exactly the inference that vectors from one may
    be compared with vectors from another. The namespace is what decides that,
    not the object.

    **Prefer `connect()`.** This constructor is public and takes `dim` on
    trust, so it is possible to build one whose namespace was asserted rather
    than measured; `connect()` is the path that measures. The tests use the
    constructor directly to build a second namespace without a second server,
    which is why it is not hidden.
    """

    def __init__(self, model: str, dim: int, endpoint: str,
                 timeout: float = DEFAULT_TIMEOUT):
        self.provider = "ollama"
        self.model = model
        self.dim = dim
        self.endpoint = endpoint
        self.timeout = timeout

    @property
    def namespace(self) -> tuple[str, str, int]:
        return (self.provider, self.model, self.dim)

    def embed(self, text: str) -> list[float]:
        """`text` -> an L2-normalised vector of length `dim`.

        Empty text short-circuits to the zero vector without a request: there is
        nothing to embed, a round trip would cost latency for a known answer,
        and the zero vector is what "no evidence" already means here.

        A response whose length differs from the dim this embedder was built
        with is refused rather than stored. The likeliest cause is the model
        behind the name changing under us mid-run -- Ollama tags are mutable --
        but a proxy or a server defect produces the same symptom, and the
        response is the same either way: a store holding two vector lengths
        under one namespace cosines wrong forever after, which is the exact
        silent-wrongness the namespace exists to prevent, arriving through the
        back door.
        """
        if not text.strip():
            return [0.0] * self.dim
        url = self.endpoint.rstrip("/") + EMBED_PATH
        data = _post(self.endpoint, EMBED_PATH,
                     {"model": self.model, "input": text}, self.timeout)
        vec = _vector_from(data, url)
        self._check_dim(vec, url)
        return l2_normalise(vec)

    def _check_dim(self, vec: list[float], url: str) -> None:
        if len(vec) != self.dim:
            raise OllamaError(
                "%s returned a %d-dimensional vector for model %r, but this "
                "run is writing under dim %d. The model behind that name "
                "changed; re-embed from scratch rather than mixing lengths."
                % (url, len(vec), self.model, self.dim))

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """One round trip for the whole batch, in the caller's order.

        Measured on this machine against bge-m3: 255.6 ms per text one at a
        time, 8.1 ms per text at batch 64 -- and the vectors are bit-identical,
        largest element difference 0.00e+00 across three probes. So this buys
        round trips and nothing else; it is not a quality trade.

        **Empty texts never leave the process, and that is where the danger is.**
        `embed` short-circuits them to the zero vector, and a batch has to do the
        same without letting the hole shift everything after it. So the empties
        are held out, the rest are sent, and the answers are put back into the
        positions they came from. Getting that wrong writes correct vectors onto
        the wrong nodes -- a store that is confidently, systematically wrong,
        with nothing raising.
        """
        out: list[list[float] | None] = [None] * len(texts)
        sending = [(i, t) for i, t in enumerate(texts) if t.strip()]
        for i, t in enumerate(texts):
            if not t.strip():
                out[i] = [0.0] * self.dim
        if sending:
            url = self.endpoint.rstrip("/") + EMBED_PATH
            data = _post(self.endpoint, EMBED_PATH,
                         {"model": self.model,
                          "input": [t for _, t in sending]}, self.timeout)
            vectors = _vectors_from(data, url, len(sending))
            for (i, _), vec in zip(sending, vectors):
                self._check_dim(vec, url)
                out[i] = l2_normalise(vec)
        if any(v is None for v in out):                      # pragma: no cover
            raise OllamaError("a text in the batch got no vector")
        return [v for v in out if v is not None]


def connect(endpoint: str, model: str, timeout: float = DEFAULT_TIMEOUT,
            declared_dim: int | None = None) -> OllamaEmbedder:
    """Probe the endpoint once, learn the dim, and hand back an embedder.

    This is the constructor to use. `OllamaEmbedder(...)` is public and takes
    `dim` on trust -- the tests need that to build a second namespace without a
    second server -- so the guarantee here is about the PATH, not about the
    class: nothing in `homegraph` builds an embedder whose dim was asserted
    rather than measured, because a typo in the config would otherwise decide
    the namespace every vector is written under, and the mismatch would surface
    as "semantic search found nothing" months later rather than as a refusal
    now.
    """
    if not endpoint:
        raise OllamaError(
            "the ollama provider needs an endpoint -- set [embeddings].endpoint "
            "(e.g. \"http://localhost:11434\"). It is never assumed.")
    if not model:
        raise OllamaError(
            "the ollama provider needs [embeddings].model -- the embedding "
            "model to ask for, e.g. \"all-minilm\".")
    url = endpoint.rstrip("/") + EMBED_PATH
    data = _post(endpoint, EMBED_PATH,
                 {"model": model, "input": PROBE_TEXT}, timeout)
    dim = len(_vector_from(data, url))
    if declared_dim is not None and declared_dim != dim:
        raise OllamaError(
            "[embeddings].dim is %d but %s embeds %r at dim %d -- the vectors "
            "would be stored and searched under different namespaces"
            % (declared_dim, endpoint, model, dim))
    return OllamaEmbedder(model=model, dim=dim, endpoint=endpoint,
                          timeout=timeout)


def from_config(cfg: dict[str, object]) -> OllamaEmbedder:
    """Build the embedder an `[embeddings] provider = "ollama"` block asks for.

    Mirrors `static_embed.from_config` on purpose, including the declared-vs-
    actual check, so the two providers refuse for the same reasons in the same
    words and neither is the one with the softer rules.
    """
    provider = cfg.get("provider")
    if provider != "ollama":
        raise OllamaError(
            "embeddings provider %r is not ollama" % (provider,))
    endpoint = cfg.get("endpoint")
    if not endpoint or not isinstance(endpoint, str):
        raise OllamaError(
            "the ollama provider needs [embeddings].endpoint pointing at a "
            "running server, e.g. \"http://localhost:11434\". It is never "
            "assumed -- a provider that reached for a default would be opening "
            "a socket nobody named.")
    model = cfg.get("model")
    declared_dim = cfg.get("dim")
    if declared_dim is not None and not isinstance(declared_dim, int):
        raise OllamaError(
            "[embeddings].dim must be an integer, got %r" % (declared_dim,))
    return connect(endpoint, str(model) if model else "",
                   declared_dim=declared_dim)
