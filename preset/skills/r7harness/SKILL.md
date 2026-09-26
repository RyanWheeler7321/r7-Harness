---
name: r7harness
description: Use only for local r7Harness setup, status, diagnostics, appearance, update checks, rollback, or removal.
---

# r7Harness management

Use this skill only when the user asks to manage the harness itself. Start with
the smallest matching command:

```text
r7harness check
r7harness status
r7harness doctor
r7harness update-check
r7harness rollback
r7harness uninstall
```

`check` is read-only. Installation, rollback, and removal must show their
local scope and respect the current approval policy. Keep profile identity,
authentication, and runtime state local; never copy those materials from a
different profile or machine.
