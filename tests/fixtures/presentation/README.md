# Presentation fixtures

Synthetic command results (`*.json`) and, beside each, the text the 0.4.1 renderers printed for
it (`*.txt`). The text files were produced by running the 0.4.1 command line and verification
renderer on these results before presentation moved into `berman.presentation`.

They are the contract for redirected output: `berman.presentation.plain` must reproduce each
`.txt` byte for byte. A difference is a change to what scripts and saved output receive, and
needs the same deliberateness as a change to a command's options. The terminal rendering is
checked against the same results by a different rule: it shows every word the plain text shows.

No result here comes from a study. Paths, runs and connections are invented.
