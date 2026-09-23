-- maverick.nvim — drive the Lightwork agent runtime from Neovim.
--
-- A thin front end over the locally installed `maverick` CLI; nothing here
-- talks to a network itself.
--
--   :MaverickStart {goal}   start a goal (prompts when no arg)
--   :MaverickStatus         runtime status + cost (split)
--   :MaverickMonitor        live plan-tree TUI (terminal split)
--   :MaverickLogs           recent run logs
--   :MaverickHalt           arm the killswitch (confirms first)
--   :MaverickUnhalt         clear the killswitch
--
-- setup{} is optional:
--   require("maverick").setup({ cli = "/path/to/maverick", max_dollars = 5 })

local M = {}

M.config = {
  cli = "maverick",
  max_dollars = nil, -- when set, passed to `maverick start --max-dollars`
}

function M.setup(opts)
  M.config = vim.tbl_deep_extend("force", M.config, opts or {})
end

local function term_run(args)
  vim.cmd("botright split")
  vim.cmd("resize 15")
  vim.fn.termopen(vim.list_extend({ M.config.cli }, args))
  vim.cmd("startinsert")
end

function M.start(goal)
  if goal == nil or goal == "" then
    vim.ui.input({ prompt = "Goal for Lightwork: " }, function(input)
      if input and input ~= "" then
        M.start(input)
      end
    end)
    return
  end
  local args = { "start", goal }
  if M.config.max_dollars then
    table.insert(args, "--max-dollars")
    table.insert(args, tostring(M.config.max_dollars))
  end
  term_run(args)
end

function M.status()
  term_run({ "status", "--cost" })
end

function M.monitor()
  term_run({ "monitor" })
end

function M.logs()
  term_run({ "logs" })
end

local function checked_halt(command, present)
  local ok, output = pcall(vim.fn.system, { M.config.cli, command, "--json" })
  local status = vim.v.shell_error
  if not ok or status ~= 0 then
    vim.notify("Lightwork outcome unconfirmed (exit " .. tostring(status) .. "). Check local state before retrying.", vim.log.levels.ERROR)
    return
  end
  local parsed, result = pcall(vim.fn.json_decode, output)
  if not parsed or type(result) ~= "table" or result.command ~= command
    or result.acknowledged ~= true or result.scope ~= "local"
    or result.halt_file_present ~= present or result.work_stopped ~= false then
    vim.notify("Local halt-file state was not confirmed. Check the CLI before retrying.", vim.log.levels.ERROR)
    return
  end
  vim.notify(present and "Local kill switch armed. Running work stops at its next checkpoint."
    or "Local kill switch cleared. Prior tasks were not restarted.", vim.log.levels.INFO)
end

function M.halt()
  vim.ui.select({ "No", "Yes — arm the local kill switch" },
    { prompt = "Arm the Lightwork killswitch?" },
    function(choice)
      if choice and choice:sub(1, 3) == "Yes" then
        checked_halt("halt", true)
      end
    end)
end

function M.unhalt()
  checked_halt("unhalt", false)
end

return M
