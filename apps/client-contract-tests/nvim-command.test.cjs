const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {lua,lauxlib,lualib,to_luastring,to_jsstring} = require('fengari');

test('actual Neovim Lua requires zero exit and matching local state before confirming',()=>{
  const L=lauxlib.luaL_newstate();lualib.luaL_openlibs(L);
  const execute=text=>{
    const status=lauxlib.luaL_dostring(L,to_luastring(text));
    assert.equal(status,lua.LUA_OK,status===lua.LUA_OK?'':to_jsstring(lua.lua_tostring(L,-1)));
  };
  execute(`
    mode = 'halt'
    notices = {}
    vim = {v={shell_error=0},log={levels={ERROR='error',INFO='info'}},
      ui={select=function(options,prompt,fn) fn('Yes') end},
      notify=function(message,level) notices[#notices+1]={message,level} end,
      fn={system=function(args)
        assert(args[3]=='--json')
        if mode=='missing' then error('missing executable') end
        vim.v.shell_error = mode=='nonzero' and 1 or mode=='signal' and 143 or 0
        return args[2]
      end,json_decode=function(command)
        if mode=='malformed' then error('malformed') end
        local present=command=='halt'
        if mode=='wrong-state' then present=false end
        return {command=command,acknowledged=true,scope='local',work_stopped=false,
          halt_file_present=present}
      end}}
  `);
  const source=fs.readFileSync(path.join(__dirname,'../nvim/lua/maverick/init.lua'),'utf8');
  execute(`maverick = (function()\n${source}\nend)()`);
  execute(`
    for _,value in ipairs({'missing','nonzero','signal','malformed','wrong-state','halt','unhalt'}) do
      mode=value
      local prior=#notices
      if mode=='unhalt' then maverick.unhalt() else maverick.halt() end
      assert(#notices==prior+1)
      local notice=notices[#notices]
      if mode=='halt' or mode=='unhalt' then
        assert(notice[2]=='info')
        assert(string.find(notice[1],mode=='halt' and 'checkpoint' or 'not restarted'))
      else assert(notice[2]=='error') end
    end
  `);
  lua.lua_close(L);
});
