# Record fixtures

Synthetic records for `tests/unit/test_schemas.py`, one folder per packaged schema. Every
file under `valid/` satisfies its schema. Every file under `invalid/` fails it with exactly
the problems `expected.json` lists for it, in that order. No file here comes from a study.

The operation records include the shapes earlier workflows wrote: a run adopted without a
reservation, an inferred ending that is not a declaration, a blank incomplete-boundary
explanation and a support-trial marker. A schema change that refuses one of them is a
compatibility break, not a cleanup.
