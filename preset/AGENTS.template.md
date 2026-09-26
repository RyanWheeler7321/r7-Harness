# Project instructions

Use this file for durable project-specific directions. Keep it about the
project rather than a particular person, computer, or account.

## Working vocabulary

- `plan` means inspect and propose an approach, no edits until asked.
- `hold off` means inspect only. Don't change files, services, browser state,
  or external systems until a later request says so.
- `take a look` means inspect the relevant owner and report what you actually
  found.
- `investigate` means trace the reported behavior, gather evidence, and explain
  the likely cause before changing anything, unless a fix was asked for.
- `update` on its own means a short status checkpoint as a visible reply. The
  task keeps going after it. `update pause` gives the checkpoint and stops.

## Helpers

Inspection helpers declare:

```text
# Files
- read-only
```

Writing helpers list only the exact files or directories they need:

```text
# Files
- file: path/to/file
- dir: path/to/directory
```

Helpers get read, search, web, and their own files only. Shell, process,
browser, and MCP work stays with the main session. Don't give two helpers
overlapping files. A helper finishes its own scope and reports what it found.
