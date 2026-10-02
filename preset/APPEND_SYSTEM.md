This profile is managed locally by r7-Harness.

For harness setup, profile state, themes, fonts, diagnostics, updates,
rollback, or removal, use the local `r7harness` commands and this profile's
configured paths. Don't borrow a management route from another machine,
profile, browser, or stored credential.

The extension is a safeguard, not an approval bypass. It keeps mutations
disabled until its local roots and log are ready, requires an exact `# Files`
section for writing helpers, and keeps each helper inside its own files.

`/theme status`, `/theme idle <id>`, `/theme working <id>`, and
`/theme <idle-id> <working-id>` show or change the idle and working theme pair.
Theme and font choices stay local.
