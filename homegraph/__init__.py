"""homegraph — a five-model knowledge graph over a home directory.

The root defaults to ~, and is overridden with $HOMEGRAPH_ROOT or by passing
`home=` to Classifier.
"""

# Keep in step with [project] version in pyproject.toml -- the MCP server
# reports this string as serverInfo.version, so a drift here is visible to
# every client that connects.
__version__ = "0.1.0"
