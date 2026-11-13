# Package refactor

The extraction at `eb8becc` preserved the earlier script layout. This refactor
makes the reusable software an installable package, with workspace data outside
the distribution. The original extraction manifest in `origin.json` remains an
unchanged historical record.

The structural checkpoint deliberately moves modules, tests, fixtures and docs
before repairing imports. `python3.14 -m unittest discover -s tests -t .` reports
32 errors at that checkpoint; the former source paths no longer exist. The
frozen collector is relocated without changing its bytes.

The fieldwork repository, its installed command, preserved runs and qualification
receipts are outside this change.
