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
"""
