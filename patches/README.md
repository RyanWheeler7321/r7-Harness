# OMP patch

`omp-18.4.4-r7h3.patch` is the source change against OMP tag `v18.4.4`. It carries the parts that can't be done cleanly from an extension:

- Task titles like `5m | Title`, or `✦ | Title` for a quick answer, plus a check that updates the estimate when a task runs over. Without an opening line the title comes from the reply's first heading
- Turn timer in the π icon, which keeps the finished time while idle, and the subagent count before the model
- The session folder on its own row under the Ctrl+P model track
- Reply style for assistant replies: colored markers, borderless tables and numbered questions (`replyStyle.enabled`, `replyStyle.colors`)
- r7-Shell support, only when `R7SHELL_SESSION` is set: bigger reply titles, pictures in replies, mouse editing in the input box and a signal when a turn finishes
- Remaining weekly usage and a compact context percentage
- One-line tool rows (`display.collapseToolRows`) with ✘ for failed and ⓘ for skipped calls
- An input box that doesn't jump when live output shrinks, editor redo (`tui.editor.redo`), and Ctrl+C clearing a draft as one step Ctrl+Z can undo
- Read-only Bash calls can run side by side, everything else stays exclusive
- Compaction writes a readable full history file and points the agent at it
- An optional relevance check that drops finished work from context when the task changes (skipped when compaction is off)
- RPC mode stops the running turn when the client disconnects instead of waiting for it to finish
- Extension hooks the harness uses (select sliders, theme and terminal palette, extra session events)
- File links under WSL open on the Windows side in Windows Terminal
- Model tweaks: per-provider model roles (`modelRolesByProvider`), an unbound key to lock the current role as the default, parallel tool calls on Codex, Opus 5.5 and Sonnet 5.5 thinking rules, GPT-6.1 Sol in the catalog, and Claude edit tools sent without strict decoding
- A fixed compaction token limit only lowers the threshold, never raises it, and usage reports refresh every 90 seconds
- Tests for the above, and upstream tests updated where the behavior changed on purpose

## Build

```bash
git clone --branch v18.4.4 --depth 1 https://github.com/can1357/oh-my-pi.git omp-18.4.4
cd omp-18.4.4
git apply --check ../r7-Harness/patches/omp-18.4.4-r7h3.patch
git apply ../r7-Harness/patches/omp-18.4.4-r7h3.patch
bun install --frozen-lockfile
cd packages/coding-agent
bun run build
```

The binary ends up at `packages/coding-agent/dist/omp`. The installer checks the source and patch hashes in `manifest.json` and won't use a build it can't verify.
