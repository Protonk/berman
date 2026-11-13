# Working on Berman core

This repository owns reusable software. Read README.md, BERMAN.md and
MAINTENANCE.md. Keep studies, local settings, native qualification and analysis
outside this repository. Tests use synthetic inputs in tools/fixtures/.

- Run `./berman verify` for software changes; report its scope and omissions.
- Never edit tools/capture_session.py. Its bytes are frozen for historical compatibility.
- Capture hooks enter through the exclusive router, never direct collectors.
- A software change does not qualify a native installation. Real hook changes need
  closed captures, an actual CLI trust review where applicable and native evidence.
- Respect preserved records in any selected workspace; do not run Git inside runs/.
- Use explicit Git repository targets. Keep independent study repositories out of
  commits, submodules and release payloads.
- Commit at useful checkpoints. Publish only when requested.
