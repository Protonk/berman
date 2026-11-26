# A release from one revision

A release should be one commit turned into files by commands, with the same files installed wherever Berman runs. Today `development/release/build COMMIT DESTINATION` makes the source archive, a tested binary for macOS on Apple silicon and one for Linux on arm64, the identity each binary reports, and `SHA256SUMS`. Everything around it is still done by hand: the version, the notes, the tag, the upload, and bringing the guest and the Mac to the released build.

## What a release consists of

Fix these for every release, and check them by command before anything is published.

- The version in `Cargo.toml` and `Cargo.lock`, advanced only for a requested release.
- `docs/releases/VERSION.md`, moved from `unreleased.md`, which then names the new latest release.
- One commit on `main` that carries both, already pushed, with a clean checkout.
- The files `development/release/build` makes from that commit, with `berman verify` from each binary reporting that commit and one source digest.

## Commands to add

1. **Prepare.** `development/release/prepare VERSION` sets the version, moves the notes, and refuses if the suite or the guest check fails. It commits nothing the maintainer has not seen.
2. **Publish, at the maintainer’s request.** `development/release/publish VERSION` checks the conditions above, refuses an existing tag or release, tags the commit, and creates the GitHub release from the built files. Publishing is the only step that needs repository credentials, and they stay on the Mac.
3. **Install what was published.** `development/release/install VERSION` downloads the release’s archive for the system it runs on, checks it against `SHA256SUMS`, installs it, and confirms the commit and source digest `berman verify` reports. Run it on the Mac and in the Lima guest, so neither is arranged by hand. It says when the installed path of the program changed, because each reviewed Codex home then needs its hook entries replaced and reviewed.
4. **Report drift.** `development/release/installed` compares the program installed on the Mac and in the guest with the latest release and says which, if either, is ahead for development.

## Decisions to make first

- **Linux floor.** A binary built on Debian 13 needs glibc 2.39. Building in an older base image lowers that. A static musl build removes it, and needs a musl C toolchain for the embedded SQLCipher and OpenSSL. Choose when a producer on an older system appears.
- **macOS distribution.** The binary has an ad hoc signature. A copy downloaded with a browser is quarantined and refused until the producer clears it; `curl` and a build from source are not affected. A Developer ID signature and notarization need an Apple Developer account.
- **x86-64.** Both systems can be covered from this Mac: macOS by cross-compiling and running the suite under Rosetta, Linux by an emulated guest or container. Either roughly doubles the time a release takes to test. Add it when there is an x86-64 producer, and then test it the same way as arm64, not less.
- **Where builds run.** Hosted runners exist for both arm64 systems and could run the suite and `development/release/build` on every push to `main`. Publishing would stay a maintainer’s command.
- **Repeatable bytes.** Check whether two builds of one commit give the same binary digest on each system. If they do, `SHA256SUMS` can be checked by anyone who builds the commit.
