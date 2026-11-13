# Record fixtures

Synthetic records for `tests/unit/test_schemas.py`, one folder per packaged schema. Every
file under `valid/` satisfies its schema. Every file under `invalid/` fails it with exactly
the problems `expected.json` lists for it, in that order. No file here comes from a study.

The operation records include the shapes earlier workflows wrote: a run adopted without a
reservation, an inferred ending that is not a declaration, a blank incomplete-boundary
explanation and a support-trial marker. The fixtures named `tolerated-by-0.4` hold values
version 0.4 converted, read as absent or never read: a text timeout, numbered steps, a
connection with no fieldwork fields, free-form notes. A schema change that refuses any of
these is a compatibility break, not a cleanup.
