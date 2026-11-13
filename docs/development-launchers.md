# Development launcher compatibility adapter

These launchers isolate ordinary Stewart development from research roots.
Their installer and probes retain Stewart-specific project checks and executable
names. They are optional compatibility code, not a generic installation step.
The fieldwork workflow uses separately configured named connections.

The offline suite runs with the developer tests in `tests/compatibility/`. The
optional installer is `python -m berman.harnesses.development.install`. Installing these launchers and
running their native --check commands are separate actions. An existing study
can retain its pinned installation while developing the standalone core.

For another project, supply suitable checks and qualify its actual access policy
before adopting an adapted launcher. Include the fieldwork, analysis and live
packet locations in the denied research roots. The core software tests do not
establish a native blinding boundary.
