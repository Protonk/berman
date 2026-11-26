# Developing Berman

Berman is one Rust program for Linux and macOS. Use the toolchain `rust-toolchain.toml` names, which rustup installs on first use, with Git, a C compiler, `make` and `perl`. The build embeds SQLCipher and the OpenSSL it needs, so both systems store the library with the same engine and the program links only the system’s C library. From the checkout run:

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo run --locked -- verify
```

The command line in `main.rs` calls `episodes.rs` for an opening, the attended and unattended sittings and the producer’s ending. `claude.rs` and `codex.rs` each hold what Berman knows about one actor’s CLI: its home, callbacks, launch arguments in both attendances, the result an unattended run reports, session store and transcript format. Start there when a harness changes. `capture.rs` attributes native events and publishes transcript generations. Installed Codex hooks run `berman capture codex`, so renaming that command breaks capture for every reviewed Codex home until its hooks are installed and reviewed again. `library.rs` is the encrypted store and its directory operations, `keys.rs` the key’s keeping, and `places.rs` where the library and the working directories live on each system. `records.rs` collects a closed episode, verifies records in the library and on disk, prepares reviews, and imports records from earlier releases. `process.rs` identifies a running actor on each system. The program carries the editable job and both examples from `src/berman/templates/`.

The suite uses real temporary Git checkouts, subprocesses, disposable homes with their own libraries, and a scripted actor built from `tests/fixtures/actor.rs`. It exercises ordinary work, episodes with no, one or several jobs, attended and unattended sittings, resume, interruption, closure, cleanup, verification in the library and on disk, review, the library’s directory operations and key, and import. The scripted actor imitates Claude Code’s print-mode result and the `codex exec` event stream, including failed, silent and refused runs, and the checkout changes between sittings. `tests/native_results.rs` keeps output recorded from the actual CLIs, reduced to the lines Berman reads. `tests/capture.rs` checks what happens to bytes across appended, rewritten and truncated transcripts and after a killed writer. `tests/search.rs` plants distinct filenames and phrases in one project’s return, telemetry and review, then searches from another project and from the home directory. A real actor login, reviewed Codex hooks, a producer-chosen project and a native sitting are separate evidence. Check a change to native output handling against the actual CLIs.

`tests/golden/` holds fixed records. Those under `python-0.26` were made by the last Python release and show that its records still verify and import. Those under `native` were exported by this program on Linux and on macOS, and each must verify on the other. When the record format changes, write new ones on each system with `cargo test --test library -- --ignored write_this_platform`. The test that keeps a key in the login’s own keychain or Secret Service is also ignored by default. Run it in a login whose store is unlocked.

`tests/scale.rs` measures peak memory, the journal’s high-water size and elapsed time for callbacks on a transcript up to the 256 MiB file limit, for closing, verifying and exporting a record at the 1 GiB limit, and for a library that runs out of disk. It prints a table and is not part of the routine suite.

```sh
cargo test --release --test scale -- --ignored --nocapture --test-threads 1
```

## Both systems from one revision

Development happens in the checkout on the Mac. The Linux build and suite run in the Lima guest from a source archive of a commit. The guest holds no Berman clone and no repository credentials. Add `--container` for a second check, which installs that revision in a Debian 13 container as the README describes and runs the suite there without a network. [Lima setup on macOS](../development/lima/README.md)

```sh
development/lima/check --container COMMIT
```

`development/release/build COMMIT DESTINATION` makes the files of a release without publishing anything: the source archive, a binary for each system built from that archive and tested where it was built, the identity each binary reports, and their SHA-256 digests. The Linux binary needs glibc 2.39 or later. The macOS binary runs on macOS 11 or later on Apple silicon and carries an ad hoc signature.

Versions use `0.YY.N` and advance only for a requested release. `berman verify` reports a digest of the sources a binary was built from, and the commit when the build was given one, which distinguishes development builds with the same version. Keep public tags and releases intact.
