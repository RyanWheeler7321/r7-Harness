![r7Harness terminal workspace](assets/r7harness-main.png)

<img src="assets/icon.svg" alt="r7Harness icon" width="96">

# r7Harness

r7Harness is a simplified version of my own OMP-based agent workspace.

I started building it because the harness around an agent matters almost as much as the model inside it.

- Compact workspace with session titles, turn timing, usage, status and a composer that stays put
- Titles that show how big the current task is (quick, check, cook or deep) with a time estimate
- Separate agent profiles, each with its own identity, config, login, sessions and local state
- Global and project instructions, reusable skills and a few workflow keywords like "update"
- Idle and working themes that follow the agent's state (`/theme` to switch), plus a list of good terminal fonts
- Readiness checks, tool limits and file ownership so subagents don't step on each other
- A pinned, hash-checked OMP build (currently 18.3.2) that's easy to roll back to an earlier build
- If RTK is installed, common read and test commands go through it for shorter output
- A small optional AutoHotkey script for opening and arranging harness windows in Windows Terminal

## Setup

It runs on WSL with Windows Terminal. You need git, Python 3 and Bun 1.4.0.

```bash
git clone https://github.com/RyanWheeler7321/r7Harness.git
cd r7Harness
curl -L -o omp-18.3.2.tar.gz https://github.com/can1357/oh-my-pi/archive/7853b4e499936f9dcc13c9b64adb55f6b342aabf.tar.gz
./install.sh --source omp-18.3.2.tar.gz --agent-name Nova --provider anthropic --model claude-sonnet-4-5
./bin/r7harness launch
```

The install checks the source against `manifest.json`, applies the patch, builds OMP next to any OMP you already have and creates a separate profile. Log in from inside it with `/login`. `./bin/r7harness doctor` checks the install and `./bin/r7harness uninstall --yes` removes the build and the files it installed, and leaves your sessions, login and logs.

## Personality

Your agent's personality lives in `PERSONALITY.md` inside its profile (`~/.omp/profiles/r7/agent` by default). Fill it in with what you want to call it, how you want it to behave and how much it should do on its own. Your identity, instructions, credentials, sessions, memory and project details stay on your own machine.

I built r7Harness around Codex, but it isn't limited to Codex. You can use Claude or any other model that works with OMP.

It's still early. The install and the main features work, but expect rough edges.

More information: [r7321.art/tools/r7harness](https://r7321.art/tools/r7harness/)

*r7Harness is an unofficial modification of [OMP](https://github.com/can1357/oh-my-pi). It isn't officially associated with the OMP project, OpenAI/Codex, Anthropic/Claude, or their developers.*
