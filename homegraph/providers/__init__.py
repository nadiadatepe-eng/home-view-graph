#!/usr/bin/env python3
"""Embedding providers: how a piece of text becomes a vector.

There is one today -- `static_embed`, a lookup table distilled offline into a
data file -- and the package exists so a second one (a network endpoint like
Ollama) can be added WITHOUT the search path learning a new shape. Every
provider hands back the same thing: an object with a `namespace` of
(provider, model, dim) and an `embed(text) -> list[float]` that returns an
L2-normalised vector. `search.vector_search` knows only that contract.

The line this package holds is `dependencies = []`. A provider may read a file
(static does) or, one day, open a socket the user explicitly configured (a
network one would). None of them may pull a model off the internet at import or
at inference: an embedding here is arithmetic over a data file the user already
has, not a download that quietly happens the first time a search is run. That
was the failure in code-review-graph #711 -- a build path that loaded a model
and cost money without being asked -- and it is designed out rather than warned
about.

Two ways in, and they exist because two callers ask different questions:

  * `from_config(cfg)` -- what the `[embeddings]` block says. Used by `embed`,
    which writes vectors and therefore has to agree with the config about the
    namespace it writes them under.
  * `from_locator(spec)` -- what the user typed on the command line. Used by
    `search`, whose help promises it reads a database path and what you name,
    never the config. A matrix path stays a matrix path; `ollama://model@host`
    is the network form. An unknown scheme is refused rather than treated as a
    filename, because a typo'd scheme silently becoming a missing-file error
    would send the reader looking for the wrong thing.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import ollama, static_embed

if TYPE_CHECKING:
    from ..search import Embedder

# Everything a caller must be ready to catch from either provider, as one name.
# A tuple rather than a shared base class: `EmbedderDataMissing` is deliberately
# a `FileNotFoundError` (a missing matrix IS a missing file, and callers that
# already handle that keep working), and bending the hierarchy so both providers
# descend from one root would change what `except FileNotFoundError` means
# elsewhere to buy nothing this tuple does not.
PROVIDER_ERRORS = (
    static_embed.EmbedderDataMissing,
    static_embed.StaticEmbedError,
    ollama.OllamaError,
)

OLLAMA_SCHEME = "ollama://"


def from_config(cfg: dict[str, object]) -> "Embedder":
    """Build the embedder a `[embeddings]` block asks for.

    The dispatch is on `provider` and nothing else. An unknown name is refused
    with the list of known ones rather than defaulted to static: silently
    picking a provider the config did not ask for is how vectors end up written
    under a namespace nobody chose.
    """
    provider = cfg.get("provider")
    if provider == "static":
        return static_embed.from_config(cfg)
    if provider == "ollama":
        return ollama.from_config(cfg)
    raise static_embed.StaticEmbedError(
        "embeddings provider %r is not known; wired up: 'static' (a distilled "
        "matrix data file) and 'ollama' (a local inference server you name "
        "with [embeddings].endpoint)" % (provider,))


def from_locator(spec: str) -> "Embedder":
    """Build the embedder a command-line `--embeddings` value names.

    Two forms, told apart by scheme and nothing cleverer:

        /path/to/matrix.json              -> static, the file
        ollama://all-minilm@localhost     -> ollama on the standard port
        ollama://all-minilm@10.0.0.5:11435 -> ollama, port named

    The PORT may be omitted; the HOST may not. That asymmetry is deliberate:
    naming a host is the act of choosing where to go, and 11434 is a convention
    about where a chosen host listens -- not a decision to reach somewhere
    nobody asked for. A missing host would be exactly that decision.
    """
    if not spec:
        raise static_embed.StaticEmbedError(
            "--embeddings needs a value: a matrix data file, or "
            "ollama://<model>@<host>[:<port>]")
    if spec.lower().startswith(OLLAMA_SCHEME):
        return ollama.connect(*parse_ollama_locator(spec))
    if "://" in spec:
        raise static_embed.StaticEmbedError(
            "unknown --embeddings scheme in %r; use a matrix data file path, "
            "or ollama://<model>@<host>[:<port>]" % spec)
    return static_embed.load(spec)


# What a hostname may contain. Deliberately narrow: letters, digits, dot and
# hyphen (DNS names and IPv4 literals), or a bracketed IPv6 literal. Anything
# else -- whitespace, a second `@`, a stray `:`, a `#`, a `/` -- is refused
# rather than interpolated into a URL string and handed to urllib, which parses
# what it is given by RFC rules and not by what the user meant.
_HOSTNAME = re.compile(r"\A[A-Za-z0-9]([A-Za-z0-9.\-]*[A-Za-z0-9])?\Z")
_IPV6 = re.compile(r"\A\[[0-9A-Fa-f:.]+\]\Z")


def parse_ollama_locator(spec: str) -> tuple[str, str]:
    """`ollama://<model>@<host>[:<port>]` -> (endpoint, model). No network.

    Split out and validated character by character because the first version of
    this built a URL by interpolation and let urllib sort it out, which is not
    parsing -- it is guessing, and it guessed differently from the reader:

      * `ollama://a@b@c` became `http://b@c:11434`, where RFC 3986 reads `b` as
        userinfo and the request goes to host **c**. A locator naming one host
        reached another.
      * `ollama://m@evil.example#@localhost` put everything after `#` in the
        fragment, so the host was `evil.example`.
      * `ollama://m@h:1:2`, `:0` and `:99999` were all accepted as ports.
      * a full-width digit passed `str.isdigit()` and then raised
        `UnicodeEncodeError` out of urllib -- a traceback on plain CLI input.
      * whitespace went straight into the URL.

    None of those are hypothetical; each was reproduced. The rule now is that a
    locator is accepted only when every part is unambiguous, and the error names
    the part that was not.
    """
    rest = spec[len(OLLAMA_SCHEME):]
    shape = ("the shape is ollama://<model>@<host>[:<port>], e.g. "
             "ollama://all-minilm@localhost")
    # rpartition, and then a check that no `@` survives in the model: with two
    # `@` there is no honest reading, so there is no reading at all.
    model, sep, host = rest.rpartition("@")
    if not sep or not model or not host:
        raise ollama.OllamaError(
            "malformed ollama locator %r -- %s" % (spec, shape))
    if "@" in model:
        raise ollama.OllamaError(
            "malformed ollama locator %r -- more than one '@', so which part "
            "is the host is ambiguous. %s" % (spec, shape))
    if model != model.strip() or not model.strip():
        raise ollama.OllamaError(
            "malformed ollama locator %r -- the model name has surrounding "
            "whitespace" % spec)

    port = str(ollama.DEFAULT_PORT)
    if host.startswith("["):                                    # IPv6 literal
        close = host.find("]")
        if close == -1:
            raise ollama.OllamaError(
                "malformed ollama locator %r -- unclosed '[' in the host"
                % spec)
        hostname, tail = host[:close + 1], host[close + 1:]
        if tail.startswith(":"):
            port = tail[1:]
        elif tail:
            raise ollama.OllamaError(
                "malformed ollama locator %r -- unexpected %r after the IPv6 "
                "address" % (spec, tail))
        if not _IPV6.match(hostname):
            raise ollama.OllamaError(
                "malformed ollama locator %r -- %r is not an IPv6 literal"
                % (spec, hostname))
    elif ":" in host:
        hostname, _, port = host.partition(":")
        if ":" in port:
            raise ollama.OllamaError(
                "malformed ollama locator %r -- more than one ':' in the host; "
                "an IPv6 address must be bracketed, as in [::1]:11434" % spec)
    else:
        hostname = host

    # `isascii()` before `isdigit()`: `str.isdigit()` is True for full-width and
    # other Unicode digits, which then raise UnicodeEncodeError deep inside
    # urllib instead of being refused here.
    if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
        raise ollama.OllamaError(
            "malformed ollama locator %r -- %r is not a port number in 1-65535"
            % (spec, port))
    if not host.startswith("[") and not _HOSTNAME.match(hostname):
        raise ollama.OllamaError(
            "malformed ollama locator %r -- %r is not a hostname (letters, "
            "digits, '.' and '-'; bracket an IPv6 address as [::1])"
            % (spec, hostname))
    return "http://%s:%s" % (hostname, port), model
