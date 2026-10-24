# Contributing

Open an issue with a minimal, synthetic reproduction and the expected behavior. Never attach private exports or credentials.

Requires Python 3.11+. Create a virtual environment, then run:

```sh
python -m pip install -e . -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

Changes to parsing, limits, output schema, or file writes need a regression test. This project uses no runtime dependencies. CI only tests and builds; it does not commit, publish, or update dependencies.
