"""Shared plumbing for every plate domain package: I/O primitives (``gh``),
presentation primitives (``render``), text/timestamp cleaning (``text``),
the JSON config (``config``), and owner-alias resolution plus listing
truncation notes (``owner``).

Domain packages (``plate.issues``, and later a ``plate.prs``) may import from
here; ``plate.core`` never imports a domain package back (see
``tests/test_boundaries.py``, the enforcement of the boundary rule in issue
#50).
"""
