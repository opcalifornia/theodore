"""Platform-agnostic analysis layer.

Nothing in this package (or its submodules) may import from theodore.resolve.
Analysis produces plain JSON; theodore.resolve/ and theodore.export/ consume
it. This boundary is what lets later phases target other NLEs or output
formats without touching the AI logic.
"""
