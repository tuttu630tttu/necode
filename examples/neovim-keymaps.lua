-- Compile and run the current C++ file with necode.
local necode_terminal
local function run_necode(paste)
  local file = vim.api.nvim_buf_get_name(0)
  if vim.bo.buftype ~= "" or not file:match("%.cpp$") then
    vim.notify("Open a .cpp file before running necode", vim.log.levels.WARN)
    return
  end
  local script = vim.fn.expand("~/git_station/necode/necode.sh")
  if vim.fn.executable(script) ~= 1 then
    vim.notify("necode.sh is not executable: " .. script, vim.log.levels.ERROR)
    return
  end
  if necode_terminal and necode_terminal:buf_valid() then
    local job = vim.b[necode_terminal.buf].terminal_job_id
    if job and vim.fn.jobwait({ job }, 0)[1] == -1 then
      vim.notify("necode is still running; finish input or stop it with Ctrl+C", vim.log.levels.WARN)
      necode_terminal:show()
      return
    end
    necode_terminal:close()
  end
  necode_terminal = nil
  local saved, err = pcall(vim.cmd, "write")
  if not saved then
    vim.notify(tostring(err), vim.log.levels.ERROR)
    return
  end
  local cmd = { script, file }
  if paste then
    cmd[#cmd + 1] = "--paste"
  end
  necode_terminal = Snacks.terminal.open(cmd, {
    cwd = vim.fn.fnamemodify(file, ":h"),
    auto_close = false,
    start_insert = true,
    auto_insert = true,
    win = { position = "bottom", height = 0.35 },
  })
end

vim.keymap.set({ "n", "i" }, "<C-l>", function()
  run_necode(true)
end, { desc = "necode: Clipboard Input" })
vim.keymap.set({ "n", "i" }, "<C-;>", function()
  run_necode(false)
end, { desc = "necode: Manual Input" })
-- Fallback for terminals that cannot distinguish Ctrl+;.
vim.keymap.set("n", "<leader>nr", function()
  run_necode(false)
end, { desc = "necode: Manual Input" })
vim.keymap.set("n", "<leader>np", function()
  run_necode(true)
end, { desc = "necode: Clipboard Input" })
