"""Rough-assembly layer (v1.5): builds cut timelines and trim proposals from
analyze/ output.

Unlike analyze/, this package is allowed to depend on theodore.resolve/
(assembly.builder talks to the Resolve API directly) -- the constraint that
matters here is the other direction: analyze/ must never import from
assembly/, so the AI analysis stays reusable by whatever consumes it next.
"""
