# Historical Frozen Reference

`opencode_sprint_loop/` preserves the historical Python controller reference.
`opencode_sprint_loop.lua/` is its separately versioned historical Neovim
client reference. Neither repository receives routine development work.

Do not modify source, tests, documentation, configuration, generated state, or
submodule pointers here unless the user explicitly requests a named change in
one of these repositories. Treat the preserved controller and thin Lua-client
architecture, CLI descriptions, and integration boundaries as historical
reference material, not current development direction.

Keep the references isolated from live MkChad configuration, runtime state,
OpenCode sessions, and credentials. Do not use them as a fallback for active
repository development or verification.
