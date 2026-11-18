# Presentation fixtures

Synthetic command results (`*.json`) and the deliberately pinned redirected text
beside each (`*.txt`), for the two results `berman.presentation` renders:
`status` and `verify`.

The status fixtures are what this version reports for a co-located workspace
made by release 0.26.3 or earlier, which it reads and never writes. They show
each connection's selected plan as its records left it, the lifecycle facts of
each listed run, and everything unsettled with the statement that it has to be
closed with the release that opened it. A run at rest without an ending or a
package and a run whose record cannot be read are listed without being called
unsettled. Historical missing assignment origins remain explicitly unrecorded.

The verification fixtures carry the checks this version runs on such a
workspace, including `settled`, and name what it never checks: hook trust,
native capture and native access.

They are the contract for redirected output: `berman.presentation.plain` must reproduce each
`.txt` byte for byte. A difference is a change to what scripts and saved output receive, and
needs the same deliberateness as a change to a command's options. The terminal rendering is
checked against the same results by a different rule: it shows every word the plain text shows.

No result here comes from a study. Paths, runs and connections are invented.
