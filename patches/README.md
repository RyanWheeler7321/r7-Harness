# OMP patch

`omp-18.3.2-r7h2.patch` is the source change against OMP tag `v18.3.2`. It carries the parts that can't be done cleanly from an extension:

- Task titles with a size suit (✦ ♥ ♦ ♠) and a time estimate, plus a check that updates the estimate when a task runs over
- Turn and steer timing in the status line
- Remaining weekly usage and a compact context percentage
- One-line tool rows (`display.collapseToolRows`) with ✘ for failed and ⓘ for skipped calls
- A composer that doesn't jump when live output shrinks, and Ctrl+Y paste from the kill ring
- Subagent count on the π icon
- Read-only Bash calls can run side by side, everything else stays exclusive
- Compaction writes a readable full history file and points the agent at it
- An optional relevance check that drops finished work from context when the task changes (skipped when compaction is off)
- RPC mode stops the running turn when the client disconnects instead of waiting for it to finish
- Extension hooks the harness uses (select sliders, theme and terminal palette, extra session events)
- File links under WSL open on the Windows side in Windows Terminal
- Model tweaks: per-provider model roles (`modelRolesByProvider`), an unbound key to lock the current role as the default, parallel tool calls on Codex, the Codex client version bumped to 0.156.0 for newer GPT-6 models, and Opus 5.5 thinking rules in the model catalog
- A fixed compaction token limit only lowers the threshold, never raises it, and usage reports refresh every 90 seconds
- Tests for the above, and upstream tests updated where the behavior changed on purpose

## Build

```bash
git clone --branch v18.3.2 --depth 1 https://github.com/can1357/oh-my-pi.git omp-18.3.2
cd omp-18.3.2
git apply --check ../r7Harness/patches/omp-18.3.2-r7h2.patch
git apply ../r7Harness/patches/omp-18.3.2-r7h2.patch
bun install --frozen-lockfile
cd packages/coding-agent
bun run build
```

The binary ends up at `packages/coding-agent/dist/omp`. The installer checks the source and patch hashes in `manifest.json` and won't use a build it can't verify.
