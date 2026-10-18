"""Root conftest: puts the repo root on sys.path so `import moonlight` works.

pytest (prepend import mode) inserts this file's directory for the conftest,
making the `moonlight` package importable from tests without an editable
install. Intentionally minimal: no fixtures here.
"""
