# Split recording published with 0.26.4

These synthetic bytes were prepared from core commit `92721df`, using
the custody test's Codex parent-generation example. The recording omits the
redundant capture reference, as early split recordings did. Release 0.26.4
verified and imported that ZIP and published the edition and selection pointer.
The custody tests pin both ZIP digests and replay the original selection.
