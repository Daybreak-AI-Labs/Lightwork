const {test} = require('node:test');
const assert = require('node:assert/strict');
const {React, native, act, create, source, connection, newer, button, deferred, text} = require('./helpers.cjs');

const goals = count => Array.from({length: count}, (_, index) => ({
  id: count - index, title: `Task ${count - index}`, status: 'active', result: null,
}));

async function fixture(initial = goals(125), {replayEffects = false} = {}) {
  let stored = initial, app, settings = connection, handler, opened;
  const calls = [], timers = new Map(), deadlines = new Map();
  let nextDeadline = 0;
  const page = ({limit, offset = 0, status = '', before}) => stored
    .filter(row => (!status || row.status === status) && (before == null || row.id < before)).slice(offset, offset + limit);
  const api = source('src/api.ts');
  const {RunsListScreen} = source('src/screens/RunsListScreen.tsx', {
    ...(replayEffects ? {react: {...React, useEffect(effect, dependencies) {
      React.useEffect(() => { const cleanup = effect(); if (cleanup) cleanup(); return effect(); }, dependencies);
    }}} : {}),
    '../api': {...api,
      listGoals: async (cfg, limit, signal, offset = 0, status = '', before) => {
        calls.push({cfg, limit, signal, offset, status, before});
        if (handler) return handler({cfg, limit, signal, offset, status, before});
        return page({limit, offset, status, before});
      },
      searchGoals: async (_cfg, query) => stored.filter(row => row.title.includes(query)),
    },
    setInterval: fn => { const id = timers.size + 1; timers.set(id, fn); return id; },
    clearInterval: id => timers.delete(id),
    setTimeout: (fn, ms) => { const id = ++nextDeadline; deadlines.set(id, {fn, ms}); return id; },
    clearTimeout: id => deadlines.delete(id),
  });
  const render = () => React.createElement(RunsListScreen, {settings, onOpenRun(id, title) { opened = {id, title}; }});
  await act(async () => { app = create(render()); });
  return {
    app, calls, api, page, opened: () => opened,
    ids: () => Array.from(app.root.findByType(native.FlatList).props.data, row => row.id),
    set: value => { stored = value; },
    handle: value => { handler = value; },
    tick: () => act(async () => { for (const timer of timers.values()) timer(); }),
    expire: () => act(async () => { for (const deadline of [...deadlines.values()]) if (deadline.ms === 20_000) deadline.fn(); }),
    more: () => act(async () => { const control = button(app, 'Load older tasks'); assert.ok(control, 'older tasks remain reachable'); await control.props.onPress(); }),
    owner: next => act(async () => { settings = next; app.update(render()); }),
    close: () => act(async () => { app.unmount(); }),
  };
}

test('125 tasks remain reachable without a missing boundary or duplicates after insertion and refresh', async () => {
  const f = await fixture();
  try {
    assert.deepEqual(f.ids(), goals(125).slice(0, 50).map(row => row.id));
    await f.more();
    assert.equal(f.ids().length, 100);
    f.set(goals(126));
    await f.tick();
    assert.ok(f.ids().includes(76), 'refresh must retain the previous first-page boundary task');
    await f.more();
    assert.deepEqual(f.ids(), goals(126).map(row => row.id));
    assert.equal(new Set(f.ids()).size, f.ids().length);
    assert.equal(button(f.app, 'Load older tasks'), undefined);
  } finally { await f.close(); }
});

test('the 125th task can be loaded and opened from the full mock journey', async () => {
  const f = await fixture(goals(125).map(row => ({...row, title: `Task ${126 - row.id}`})));
  try {
    assert.equal(button(f.app, 'Task 125'), undefined);
    await f.more();
    await f.more();
    await act(async () => { button(f.app, 'Task 125').props.onPress(); });
    assert.deepEqual(f.opened(), {id: 1, title: 'Task 125'});
  } finally { await f.close(); }
});

test('ID continuation stays complete when insertion and deletion occur between page reads', async () => {
  const f = await fixture();
  try {
    let changed = false;
    f.handle(request => {
      if (request.before !== undefined && !changed) {
        changed = true;
        f.set(goals(126).filter(row => row.id !== 100));
      }
      return f.page(request);
    });
    await f.more();
    assert.ok(changed);
    assert.ok(f.ids().includes(75));
    assert.equal(new Set(f.ids()).size, f.ids().length);
    await f.tick();
    await f.more();
    assert.deepEqual(f.ids(), goals(126).filter(row => row.id !== 100).map(row => row.id));
    assert.ok(f.calls.every(call => call.offset === 0));
  } finally { await f.close(); }
});

for (const failure of [401, 403, 'unsupported']) {
  test(`access loss ${failure} on an older page clears all previously displayed content`, async () => {
    const f = await fixture();
    try {
      await f.more();
      f.handle(request => request.before === undefined ? f.page(request)
        : Promise.reject(new f.api.ApiError('Access is no longer available.', typeof failure === 'number' ? failure : undefined,
          failure === 'unsupported' ? 'unsupported' : 'access')));
      await f.tick();
      assert.deepEqual(f.ids(), []);
      assert.doesNotMatch(text(f.app), /Task 125|Task 50|saved offline|may be out of date/);
      assert.match(text(f.app), /Access is no longer available/);
      assert.equal(button(f.app, 'Load older tasks'), undefined);
      if (failure === 'unsupported') {
        assert.equal(button(f.app, 'Try again'), undefined, 'unsupported native builds require the existing update path');
        return;
      }
      f.handle(null);
      f.set(goals(3));
      await act(async () => { button(f.app, 'Try again').props.onPress(); });
      assert.deepEqual(f.ids(), [3, 2, 1]);
    } finally { await f.close(); }
  });
}

test('changing identity aborts a pending older page and late content cannot republish', async () => {
  const f = await fixture();
  const pending = deferred(), replacement = deferred();
  try {
    const obsoletePress = button(f.app, 'Load older tasks').props.onPress;
    f.handle(request => request.cfg === newer ? replacement.promise
      : request.before === undefined ? f.page(request) : pending.promise);
    await f.more();
    const olderRequest = f.calls.at(-1);
    await f.owner(newer);
    assert.ok(olderRequest.signal.aborted);
    assert.deepEqual(f.ids(), []);
    const requestCount = f.calls.length;
    await act(async () => { obsoletePress(); });
    assert.equal(f.calls.length, requestCount, 'an obsolete account button cannot refresh the current account');
    await act(async () => { pending.resolve(goals(75).slice(0, 50)); });
    assert.deepEqual(f.ids(), []);
    await act(async () => { replacement.resolve([{id: 400, title: 'New account task', status: 'done'}]); });
    assert.deepEqual(f.ids(), [400]);
    assert.equal(button(f.app, 'Load older tasks'), undefined);
  } finally { await f.close(); }
});

for (const change of ['status', 'search']) {
  test(`${change} changes discard a pending older page instead of restoring its content`, async () => {
    const f = await fixture();
    const pending = deferred();
    try {
      f.handle(request => request.status === 'done' ? []
        : request.before === undefined ? f.page(request) : pending.promise);
      await f.more();
      const olderRequest = f.calls.at(-1);
      if (change === 'status') await act(async () => { button(f.app, 'Finished').props.onPress(); });
      else {
        await act(async () => { f.app.root.findByType('TextInput').props.onChangeText('no match'); });
        await act(async () => { button(f.app, 'Search').props.onPress(); });
      }
      assert.ok(olderRequest.signal.aborted);
      await act(async () => { pending.resolve(goals(75).slice(0, 50)); });
      assert.deepEqual(f.ids(), []);
      assert.match(text(f.app), /No matching tasks/);
    } finally { await f.close(); }
  });
}

test('an old server ignoring the cursor fails visibly without duplicate or partial publication', async () => {
  const f = await fixture();
  try {
    f.handle(() => goals(125).slice(0, 50));
    await f.more();
    assert.deepEqual(f.ids(), goals(125).slice(0, 50).map(row => row.id));
    assert.match(text(f.app), /ask your administrator to update the dashboard/);
    assert.equal(f.calls.length, 3);
  } finally { await f.close(); }
});

test('the shared window deadline stops paging and leaves a working retry', async () => {
  const f = await fixture();
  const pending = deferred();
  try {
    f.handle(request => request.before === undefined ? f.page(request) : pending.promise);
    await f.more();
    const stale = f.calls.at(-1);
    await f.expire();
    assert.ok(stale.signal.aborted);
    assert.match(text(f.app), /took too long/);
    assert.ok(button(f.app, 'Load older tasks'), 'a timed-out ignored abort must not leave the footer busy');
    f.handle(null);
    await act(async () => { button(f.app, 'Try again').props.onPress(); });
    assert.equal(f.ids().length, 100);
    await act(async () => { pending.resolve(goals(75).slice(0, 50)); });
    assert.equal(f.ids().length, 100);
    await f.more();
    assert.deepEqual(f.ids(), goals(125).map(row => row.id));
  } finally { await f.close(); }
});

test('superseding an older-page refresh rejects its late response and duplicate taps', async () => {
  const f = await fixture();
  const first = deferred(), latest = deferred();
  try {
    let continuations = 0;
    f.handle(request => request.before === undefined ? f.page(request)
      : ++continuations === 1 ? first.promise : latest.promise);
    const press = button(f.app, 'Load older tasks').props.onPress;
    await act(async () => { press(); press(); });
    assert.equal(continuations, 1, 'double tap admits one continuation');
    const obsolete = f.calls.at(-1);
    // Recreate the app lifecycle's explicit foreground refresh while a page is
    // pending; automatic interval ticks deliberately do not overlap requests.
    await act(async () => { native.AppState.emit('active'); });
    assert.ok(obsolete.signal.aborted);
    assert.equal(continuations, 2);
    await act(async () => { first.resolve(goals(75).slice(0, 50)); });
    assert.equal(f.ids().length, 50);
    assert.equal(button(f.app, 'Load older tasks'), undefined, 'obsolete finally cannot clear the latest busy footer');
    await act(async () => { latest.resolve(goals(75).slice(0, 50)); });
    assert.equal(f.ids().length, 100);
    assert.ok(button(f.app, 'Load older tasks'));
  } finally { await f.close(); }
});

test('unmount aborts pagination and late replies admit no further pages', async () => {
  const f = await fixture();
  const pending = deferred();
  f.handle(request => request.before === undefined ? f.page(request) : pending.promise);
  await f.more();
  const last = f.calls.at(-1), count = f.calls.length;
  await f.close();
  assert.ok(last.signal.aborted);
  await act(async () => { pending.resolve(goals(75).slice(0, 50)); });
  assert.equal(f.calls.length, count);
});

test('replayed effect cleanup and setup restores current pagination ownership', async () => {
  const f = await fixture(goals(125), {replayEffects: true});
  try {
    await f.more();
    await f.more();
    assert.deepEqual(f.ids(), goals(125).map(row => row.id));
    assert.equal(button(f.app, 'Load older tasks'), undefined);
    assert.deepEqual(f.app.root.findByType(native.FlatList).props.maintainVisibleContentPosition.minIndexForVisible, 0);
  } finally { await f.close(); }
});

test('refresh removes deleted and newly inaccessible older tasks before continuing', async () => {
  const f = await fixture();
  try {
    await f.more();
    const current = goals(125).filter(row => ![125, 76, 50].includes(row.id));
    f.set(current);
    await f.tick();
    assert.ok(!f.ids().includes(50), 'an older removed row must not remain in an append-only cache');
    await f.more();
    assert.deepEqual(f.ids(), current.map(row => row.id));
  } finally { await f.close(); }
});

test('search with no matches has different guidance from an empty account', async () => {
  const f = await fixture();
  try {
    await act(async () => { f.app.root.findByType('TextInput').props.onChangeText('not in this account'); });
    await act(async () => { button(f.app, 'Search').props.onPress(); });
    assert.deepEqual(f.ids(), []);
    assert.match(text(f.app), /No matching tasks/);
    assert.doesNotMatch(text(f.app), /No tasks yet/);
  } finally { await f.close(); }
});
