#!/usr/bin/env python3
"""Mutation test for CP-I1 -- Ollama as an opt-in embeddings provider.

Every gate in test_i1 names a specific way a network-backed provider goes
quietly wrong. A gate that cannot actually fail is worse than none, so each
mutation below manufactures exactly that failure -- normalisation dropped, a
dim taken on the config's word, a mid-run model swap waved through, an error
response parsed for a vector that is not there, an unreachable endpoint
swallowed into zeros, a socket opened at import -- and names the check that
must go red.

The one that matters most is the first. `l2_normalise` returning its input is
not a crash and not a wrong number that anything asserts on: it is a ranking
that quietly prefers long vectors to close ones. The CP-I1 fixture exists so that
this mutation flips an ORDER, because an order is the only thing that makes the
difference observable.

Run:
    python3 tests/mutate_i1.py
"""
from __future__ import annotations

import os
import sys

MUTATIONS = [
    # THE one. Normalisation is skipped, so `search._cosine` -- a plain dot
    # product over what it was promised are unit vectors -- starts ranking by
    # magnitude. The decoy is 6x longer and 0.6 as close, so it wins, and the
    # only symptom anywhere is that the order changed.
    ("l2_normalise returns its input, so magnitude decides the ranking",
     "homegraph/providers/ollama.py",
     "    return [x / norm for x in vec]",
     "    return vec  # mutated: normalisation dropped",
     "cosine order wins: target before decoy"),

    # The declared dim is trusted instead of checked against the server, so a
    # typo in config.toml silently picks the namespace every vector is written
    # under and every later search misses.
    ("a declared dim is taken on trust instead of checked",
     "homegraph/providers/ollama.py",
     "    if declared_dim is not None and declared_dim != dim:",
     "    if False:  # mutated: declared dim trusted",
     "a declared dim that disagrees is refused"),

    # A model swapped behind its name mid-run is accepted, mixing two vector
    # lengths into one namespace -- a store that cosines wrong forever after.
    ("a wrong-length vector mid-run is stored instead of refused",
     "homegraph/providers/ollama.py",
     "        if len(vec) != self.dim:",
     "        if False:  # mutated: length change accepted",
     "a vector of the wrong length mid-run is refused"),

    # Ollama reports some failures with HTTP 200 and an `error` field. Ignoring
    # it means parsing an error body for a vector.
    ("a 200 carrying an `error` field is parsed as a success",
     "homegraph/providers/ollama.py",
     '    if data.get("error"):',
     "    if False:  # mutated: error field ignored",
     "a 200 with an `error` field is a failure, not a vector"),

    # The batch contract goes slack: whatever came back, take the first one.
    # A server that answered about a different input would be believed.
    ("the one-vector-per-one-input contract is relaxed to [0]",
     "homegraph/providers/ollama.py",
     "    if not isinstance(vectors, list) or len(vectors) != 1:",
     "    if not isinstance(vectors, list) or not vectors:",
     "two vectors for one input is refused, not [0]"),

    # An unreachable endpoint becomes a zero vector instead of a refusal: the
    # command exits 0, writes zeros for every node, and `search` then ranks
    # nothing while reporting that the vector path ran.
    ("an unreachable endpoint is swallowed into a zero vector",
     "homegraph/providers/ollama.py",
     "        raise OllamaUnreachable(",
     "        return {\"embeddings\": [[0.0, 0.0]]}  # mutated\n        raise OllamaUnreachable(",
     "a dead endpoint raises OllamaUnreachable"),

    # The endpoint is defaulted when the config omits it -- a socket opened to
    # a host nobody named, which is the whole failure this package designs out.
    ("a missing endpoint is defaulted instead of refused",
     "homegraph/providers/ollama.py",
     '    if not endpoint or not isinstance(endpoint, str):',
     '    endpoint = endpoint or "http://localhost:11434"  # mutated\n'
     '    if False:',
     "a config with no endpoint is refused, never defaulted"),

    # A probe at import time: helpful-looking, and exactly the code-review-graph
    # #711 shape -- a module that reaches for the network because it was
    # imported, not because it was asked.
    ("the provider probes the network at import time",
     "homegraph/providers/ollama.py",
     "DEFAULT_PORT = 11434",
     "DEFAULT_PORT = 11434\n"
     "try:\n"
     "    urllib.request.urlopen('http://127.0.0.1:11434/api/embed', timeout=1)\n"
     "except Exception:\n"
     "    pass",
     "importing the ollama provider opens no socket"),

    # An unknown scheme falls through to the static loader, so `s3://...`
    # reports "no such file" about something that was never a path.
    ("an unknown locator scheme is read as a filename",
     "homegraph/providers/__init__.py",
     '    if "://" in spec:',
     "    if False:  # mutated: unknown schemes fall through to a path",
     "locator refused: unknown scheme"),

    # The mid-run failure escapes as a traceback rather than exit 2, so the
    # command's contract ("refuses, and says nothing was written") is gone.
    ("a provider failure mid-embed escapes instead of exiting 2",
     "homegraph/cli.py",
     "        except providers.PROVIDER_ERRORS as exc:\n"
     "            # A provider that dies mid-store leaves NOTHING behind",
     "        except ZeroDivisionError as exc:  # mutated: no longer catches\n"
     "            # A provider that dies mid-store leaves NOTHING behind",
     "a mid-run failure exits 2"),

    # The model name is dropped from the namespace, so vectors written by one
    # model are served under another's query -- confidently, and wrongly.
    ("the namespace stops carrying the model name",
     "homegraph/providers/ollama.py",
     "        return (self.provider, self.model, self.dim)",
     '        return (self.provider, "fixed", self.dim)  # mutated',
     "a different model returns None, not stale vectors"),

    # ---- added after the CP-I1 audit -------------------------------------
    # The two below are why the ordering check alone was not enough. Both
    # normalise -- with the wrong divisor -- so the decoy still shrinks relative
    # to the target and the ORDER is unchanged. Only the stored norm sees them.
    ("normalisation uses L-infinity, so vectors are not unit length",
     "homegraph/providers/ollama.py",
     "    norm = math.sqrt(sum(x * x for x in vec))",
     "    norm = max((abs(x) for x in vec), default=0.0)  # mutated: L-inf",
     "every stored vector is unit length"),

    ("normalisation skips vectors that are already short",
     "homegraph/providers/ollama.py",
     "    if norm == 0.0:\n        return vec",
     "    if norm <= 1.0:  # mutated: short vectors pass through\n        return vec",
     "every stored vector is unit length"),

    # A degenerate model fills the namespace with vectors that cosine to 0.0
    # against everything, and `--mode vector` then reports lexical order as a
    # semantic ranking.
    ("zero vectors are stored instead of skipped",
     "homegraph/cli.py",
     # Indented one level deeper since CP-BATCH moved the write into a batch
     # loop. The needle stopped matching, and an unappliable mutation counts as
     # a survivor -- which is how the sweep reported it.
     "                if not any(vec):\n                    degenerate += 1\n                    continue",
     "                if False:  # mutated: zero vectors stored\n                    degenerate += 1\n                    continue",
     "a model answering only zero vectors exits 2"),

    # http.client.HTTPException is not an OSError, so without its own clause it
    # escapes the provider taxonomy as a traceback.
    ("a non-HTTP response escapes the provider's exception taxonomy",
     "homegraph/providers/ollama.py",
     "    except http.client.HTTPException as exc:",
     "    except ZeroDivisionError as exc:  # mutated: HTTPException escapes",
     "a truncated HTTP body is refused, not a traceback"),

    # `search` reaches the provider twice; dropping the second handler is a
    # traceback where every other failure exits 2.
    ("search stops catching provider failures during the query",
     "homegraph/cli.py",
     "        except providers.PROVIDER_ERRORS as exc:\n"
     "            print(\"%s\" % exc, file=sys.stderr)\n"
     "            return 2\n"
     "        for w in res.warnings:",
     "        except ZeroDivisionError as exc:  # mutated\n"
     "            print(\"%s\" % exc, file=sys.stderr)\n"
     "            return 2\n"
     "        for w in res.warnings:",
     "search exits 2 when the provider dies mid-query"),

    # Locator validation, one rule at a time. Each of these was a real misparse
    # before the audit, not a hypothetical.
    ("a second @ is allowed, so the host is whatever urllib decides",
     "homegraph/providers/__init__.py",
     '    if "@" in model:',
     "    if False:  # mutated: two @ accepted",
     "locator refused: two @, ambiguous host"),

    ("the port range check is dropped",
     "homegraph/providers/__init__.py",
     " or not 1 <= int(port) <= 65535:",
     ":  # mutated: any port number accepted",
     "locator refused: port out of range"),

    ("the hostname shape check is dropped",
     "homegraph/providers/__init__.py",
     '    if not host.startswith("[") and not _HOSTNAME.match(hostname):',
     "    if False:  # mutated: any host string",
     "locator refused: whitespace in the host"),

    # A matrix file could otherwise declare itself into the network provider's
    # namespace.
    ("a static matrix may declare any provider it likes",
     "homegraph/providers/static_embed.py",
     '    if emb.provider != "static":',
     "    if False:  # mutated: file's provider not checked",
     "a static matrix cannot declare another provider"),

    # Back to classifying any failure mentioning "model" as a missing model.
    ("every failure mentioning 'model' becomes `ollama pull`",
     "homegraph/providers/ollama.py",
     '    if code == 404 or (code == 400 and (names_model or "model" in said)):',
     '    if code in (404, 400) or "model" in said or "embed" in said:  # mutated',
     "a 500 mentioning 'model' does not say `ollama pull`"),

    # visualize stops naming the locator, and falls through to the static
    # loader -- which also exits 2, so only the REASON can catch this.
    ("visualize stops distinguishing a locator from a path",
     "homegraph/cli.py",
     '    if emb and "://" in emb:',
     "    if False:  # mutated: locator falls through to the matrix loader",
     "and the refusal names the locator, not a missing file"),

    ("visualize loses its handler for a missing matrix",
     "homegraph/cli.py",
     "                        embeddings=emb)\n"
     "    except providers.PROVIDER_ERRORS as exc:",
     "                        embeddings=emb)\n"
     "    except ZeroDivisionError as exc:  # mutated",
     "visualize exits 2 on a missing matrix instead of a traceback"),
    # CP-BATCH. Alignment is the whole risk of batching: a single call cannot
    # confuse two texts, a batch can, and a misaligned one writes correct
    # vectors onto the wrong nodes with nothing raising.
    ("the batch comes back in the server's order, not the caller's",
     "homegraph/providers/ollama.py",
     "            for (i, _), vec in zip(sending, vectors):",
     "            for (i, _), vec in zip(sending, reversed(vectors)):  # mutated",
     "the batch keeps the caller's order"),

    ("an empty text is sent instead of short-circuited",
     "homegraph/providers/ollama.py",
     '        sending = [(i, t) for i, t in enumerate(texts) if t.strip()]',
     "        sending = list(enumerate(texts))  # mutated: empties sent too",
     "empty texts are not sent to the server"),

    ("a short batch is truncated instead of refused",
     "homegraph/providers/ollama.py",
     "    if not isinstance(vectors, list) or len(vectors) != want:",
     "    if not isinstance(vectors, list):  # mutated: count no longer checked",
     "a batch answered short is refused, not truncated"),

    ("the dim is checked on the first vector only",
     "homegraph/providers/ollama.py",
     "                self._check_dim(vec, url)\n                out[i] = l2_normalise(vec)",
     "                out[i] = l2_normalise(vec)  # mutated: dim unchecked",
     "the dim is checked on every vector, not just the first"),
    # codex found this one: the loop takes any provider with `embed_many`, and
    # `zip` stops at the shorter side without a word.
    ("the write loop trusts the provider's vector count",
     "homegraph/cli.py",
     "            if len(vecs) != len(chunk):",
     "            if False:  # mutated: count taken on trust",
     "the write loop refuses a provider that returns too few vectors"),
]

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from mutate import run                                       # noqa: E402

if __name__ == "__main__":
    sys.exit(run(MUTATIONS, "test_i1.py", prefix="muti1-", timeout=300))
