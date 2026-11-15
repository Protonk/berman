# Presentation fixtures

Synthetic command results (`*.json`) and the deliberately pinned redirected text
beside each (`*.txt`). These began with the 0.4.1 command-line and verification
renderers before presentation moved into `berman.presentation`.

The optional-study-plans change updates assignment output to show retained recipe
and origin, represents absent/exhausted plans without a fictitious next role,
reports selected-plan errors separately, and labels direct-assignment command
templates requiring a recipe choice. The optional-plans fixtures also retain a
prepared launch beside a broken plan, and show open-engagement templates without
a plan. Historical missing assignment origins remain explicitly unrecorded.

They are the contract for redirected output: `berman.presentation.plain` must reproduce each
`.txt` byte for byte. A difference is a change to what scripts and saved output receive, and
needs the same deliberateness as a change to a command's options. The terminal rendering is
checked against the same results by a different rule: it shows every word the plain text shows.

No result here comes from a study. Paths, runs and connections are invented.
