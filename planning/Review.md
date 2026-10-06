# Review of changes since HEAD

**Summary:** The changes add an automatic stop hook and review tooling, plus open design questions in the implementation plan. The hook is configured to run a reviewer with full approval and sandbox bypass, and the reviewer instructions contain a malformed command.

## Files changed

- `.claude/settings.json`
- `.gitignore`
- `.claude/agents/reviewer.md`
- `.claude/commands/doc-review.md`
- `.claude/hooks/review-changes.sh`
- `.claude/skills/cerebras/SKILL.md`
- `planning/PLAN.md`

## High

1. **The automatic reviewer runs with unrestricted permissions** — `.claude/settings.json:13`. The Stop hook invokes `codex exec --dangerously-bypass-approvals-and-sandbox` on repository changes. Changed files can contain adversarial instructions, and the reviewer has the ability to modify files beyond the requested report. Run the reviewer with a read-only or otherwise restricted mode and limit its write target to `planning/Review.md`.

## Medium

2. **The reviewer agent's required command has broken nested quoting** — `.claude/agents/reviewer.md:8`. The outer double-quoted command contains an unescaped double-quoted prompt, so it is not a valid single shell command as written. Use correct shell quoting (or pass the prompt through a file/stdin) and remove the approval/sandbox bypass.

3. **The review script can send tracked secrets to the model** — `.claude/hooks/review-changes.sh:29-33`. Secret-like filenames are filtered only for untracked files; the tracked `git diff HEAD` includes every changed path. Apply the same exclusions to tracked changes or explicitly construct a filtered path list before sending the diff.

4. **Review output path casing is inconsistent** — `.claude/settings.json:13`, `.claude/agents/reviewer.md:8`, `.claude/hooks/review-changes.sh:17`. The requested output is `planning/Review.md`, but the agent and script specify `planning/REVIEW.md`. These resolve to one file on common Windows setups but can become separate files on case-sensitive systems. Standardize the exact path.

## Low

5. **The configured Stop hook runs on every turn, even when the diff has not changed** — `.claude/settings.json:7-15`. The hash-based no-change and duplicate-review checks exist only in `review-changes.sh`, which the settings file does not invoke. Connect the hook to that guard or add equivalent gating to avoid unnecessary reviewer launches and repeated report writes.

6. **The new plan section contains recommendations that conflict with existing requirements** — `planning/PLAN.md:508-515`. For example, it proposes dropping the root `docker-compose.yml` while the earlier plan specifies it, and marks other API and behavior details as decisions. Resolve these before implementation or clearly label the section as non-normative review notes so agents do not treat both instructions as requirements.

No tests were run; this was a review-only task.
