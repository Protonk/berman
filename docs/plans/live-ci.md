# Follow-on plan for unattended live CI

1. **Give Codex the challenge in its only working session.** Today CI opens
   Codex for `/hooks`, then opens it again with the prepared assignment. Test
   whether an already reviewed router works from a fresh fixture checkout with
   a single `codex exec` launch. Keep first sign-in and review of changed hooks
   as explicit setup. Check the installed router and sign-in before launch,
   and use the attributed callbacks to establish that the hooks actually ran.

2. **Run both workers with stdin closed.** Exercise the executor’s existing
   one-turn path with Luna (`codex exec`) and Haiku (`claude --print`).
   Confirm that each can read the challenge, edit, run the check, write its
   note and exit without terminal input. If a permission prompt blocks the
   run, identify the specific access needed in the CI fixture. Keep the
   existing checks for capture, closeout, and verification of the detached
   recording and reading edition.

3. **Let live runs trust earlier source checks.** Split source testing from
   wheel building. Keep the full suite in the default deterministic CI.
   `--live codex` and `--live claude` should run only the selected engagement
   against a candidate verified on both hosts. Mark source checks as
   `not run` in that receipt. Trust the operator’s decision to run the suite
   separately and skip storing or looking up previous results.

4. **Add `--all-live`.** Build and deploy once, then run Codex and Claude
   sequentially with separate challenges, checkouts and receipts. Attempt
   both even if the first worker fails. Print each outcome and receipt path,
   and exit successfully only when both pass. Try the combined command from
   a shell without a TTY as the final check.
