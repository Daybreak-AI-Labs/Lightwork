# maverick.nvim

Drive the [Lightwork](https://github.com/Daybreak-AI-Labs/Lightwork) agent runtime
from Neovim — a thin front end over the locally installed `maverick` CLI.

## Install

lazy.nvim:

```lua
{
  dir = "/path/to/Lightwork/apps/nvim",  -- or your fork/checkout
  config = function()
    require("maverick").setup({
      cli = "maverick",   -- path to the CLI
      max_dollars = nil,  -- optional per-run spend cap
      control_timeout_ms = 30000, -- halt/unhalt supervision deadline
    })
  end,
}
```

## Commands

| Command | What it does |
|---|---|
| `:MaverickStart {goal}` | start a goal (prompts when no arg) |
| `:MaverickStatus` | runtime status + cost |
| `:MaverickMonitor` | live plan-tree TUI in a terminal split |
| `:MaverickLogs` | recent run logs |
| `:MaverickHalt` | arm the killswitch (confirms first) |
| `:MaverickUnhalt` | clear the killswitch |
| `:MaverickCancelControl` | stop waiting for halt/unhalt; outcome remains unconfirmed |

Halt/unhalt use asynchronous owned jobs, a 30-second default deadline (bounded to
100–120000 ms), and 64 KiB response limits. They announce progress and preserve
the exact acknowledgement checks. Timeout, cancellation, nonzero exit and missing
acknowledgement mean **unconfirmed**; inspect local state before retrying. Late
callbacks cannot overwrite a newer control. Status/monitor already use terminal
jobs and remain independent of the editor event loop. Closing the terminal stops
its local supervision, not external effects.

The asynchronous job fixtures in `../client-contract-tests/nvim-command.test.cjs`
are authored but unrun pending the requested later testing phase.

Classic Vim (non-Neovim) users: the CLI works in any `:terminal` —
`maverick monitor` is the same TUI.

See the [client request and acknowledgement contract](../../docs/client-request-lifecycle.md) for replay, deadlines, access errors, and platform qualification limits.
