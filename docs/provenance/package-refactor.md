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

The installed package is version 0.2.0. Its command and hook entry points use
module imports; source and workspace paths are separate. Developer tests now run
with unittest. Product verification checks installed resources and workspace
facts without importing or executing checkout tests.

The test repair preserves lifecycle, ownership, locking, recovery, native-format
simulation and archive checks. Five tests for unittest-output parsing and nested
verification guards were removed with that product behavior. Three interpreter
bootstrap tests became one installed archive-command regression: archive code
stays with the active installation. The helper AST/bytecode-equality test was
replaced by a direct frozen-byte check; observable serialization, digests and
filesystem refusals remain. Import boundaries now follow package dependencies,
including complete fingerprint coverage of code imported by live hook modules.

Shared fixtures live in tests/support. The synthetic example is exercised through
preparation, generated hook callbacks, preservation, archive building and installed
diagnostics. Its simulated access receipt and callbacks establish no native trust.

Final validation on Python 3.14 passed all 282 tests. Both source distribution and
wheel built successfully; the wheel contains 39 runtime Python files, matching
the source hashes, and no tests, examples or research data. A clean environment
ran the installed console command and generated module hooks from an unrelated
directory while macOS denied reads of the source/research repositories and native
harness homes. The example's capture, preservation and archive verification passed.

[The validation receipt](package-verification.json) records the runtime identity
and scope. Package verification passed. Synthetic workspace verification remained
incomplete as intended: native access was not run, and hook trust and native
capture were not established. Actual CLI trust review, native support trials and
installed development-launcher checks were not performed. All 31 pinned fieldwork
runtime files and the existing installed command were unchanged.
