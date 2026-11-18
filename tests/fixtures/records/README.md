# Record fixtures

Synthetic records for `tests/unit/test_schemas.py`, one folder per packaged schema. Every
file under `valid/` satisfies its schema. Every file under `invalid/` fails it with exactly
the problems `expected.json` lists for it, in that order. No file here comes from a study.
