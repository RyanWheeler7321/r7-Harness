![r7Harness terminal workspace](assets/r7harness-main.png)

<img src="assets/icon.svg" alt="r7Harness icon" width="96">

# r7Harness

r7Harness is a simplified version of my own agent setup, built on OMP.

I built it because an agent's behavior depends heavily on the environment it runs in, and a better-tuned harness gets much better results.

- Compact layout with session titles, turn timing, usage, status and an input box that stays put
- Titles that show how big the current task is (quick, check, cook or deep) with a time estimate
- Separate profiles for each agent, with their own identity, config, login and sessions
- Global and project instructions, skills, and a few keywords like "update"
- Idle and working themes that change with what the agent is doing (`/theme` to switch), plus a list of good terminal fonts
- It won't change anything until setup checks pass, tools are limited in what they can change, and subagents can't edit the same files
- Stays on one exact OMP version (currently 18.3.2), and it's easy to roll back to an earlier one
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

The install checks the source against `manifest.json`, applies the patch, builds OMP next to any OMP you already have and creates a separate profile. Log in from inside it with `/login`. `./bin/r7harness doctor` checks the install and `./bin/r7harness uninstall --yes` removes the build and the files it installed, and keeps your sessions, login and logs.

## Personality

Your agent's personality lives in `PERSONALITY.md` inside its profile (`~/.omp/profiles/r7/agent` by default). Fill it in with what you want to call it, how you want it to behave and how much it should do on its own. Your identity, instructions, credentials, sessions, memory and project details stay on your own machine.

I built r7Harness around Codex, but it isn't limited to Codex. You can use Claude or any other model that works with OMP.

It's still early. The install and the main features work, but expect rough edges.

More information: [r7321.art/tools/r7harness](https://r7321.art/tools/r7harness/)

*r7Harness is an unofficial modification of [OMP](https://github.com/can1357/oh-my-pi). It isn't officially associated with the OMP project, OpenAI/Codex, Anthropic/Claude, or their developers.*
