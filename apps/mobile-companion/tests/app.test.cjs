const { test } = require('node:test');
const assert = require('node:assert/strict');
const { React, act, create, source, deferred, connection, newer, text, tab, button } = require('./helpers.cjs');
const mocks = settings => ({ './src/settings': settings, '../settings': settings });

test('startup keychain errors remain visible and retryable', async () => {
  let calls = 0;
  const App = source('App.tsx', mocks({ loadSettings: async () => {
    if (++calls === 1) throw new Error('synthetic locked store');
    return connection;
  } })).default;
  let app;
  await act(async () => { app = create(React.createElement(App)); });
  assert.match(text(app), /Could not load connection settings/);
  await act(async () => { button(app, 'Retry loading settings').props.onPress(); });
  assert.equal(app.root.findByType('RunsListScreen').props.settings, connection);
  app.unmount();
});

test('settings load failure prevents overwriting unread values and permits retry', async () => {
  let calls = 0;
  const { SettingsScreen } = source('src/screens/SettingsScreen.tsx', mocks({
    loadSettings: async () => { if (++calls === 1) throw new Error('synthetic locked store'); return connection; },
    saveSettings: async () => assert.fail('must not overwrite unread settings'),
  }));
  let app;
  await act(async () => { app = create(React.createElement(SettingsScreen, { onSaved() {} })); });
  assert.match(text(app), /Could not load saved settings/);
  assert.equal(app.root.findAllByType('TextInput')[0].props.editable, false);
  assert.equal(button(app, 'Save').props.disabled, true);
  await act(async () => { button(app, 'Retry loading settings').props.onPress(); });
  assert.equal(app.root.findAllByType('TextInput')[0].props.value, connection.baseUrl);
  assert.equal(app.root.findAllByType('TextInput')[0].props.editable, true);
  app.unmount();
});

test('save failure retains entered values and retry success confirms the save', async () => {
  let saves = 0, callbacks = 0;
  const { SettingsScreen } = source('src/screens/SettingsScreen.tsx', mocks({
    loadSettings: async () => connection,
    saveSettings: async () => { if (++saves === 1) throw new Error('synthetic write failure'); },
  }));
  let app;
  await act(async () => { app = create(React.createElement(SettingsScreen, { onSaved() { callbacks++; } })); });
  await act(async () => { app.root.findAllByType('TextInput')[0].props.onChangeText(newer.baseUrl); });
  await act(async () => { await button(app, 'Save').props.onPress(); });
  assert.match(text(app), /Could not save settings/);
  assert.equal(app.root.findAllByType('TextInput')[0].props.value, newer.baseUrl);
  assert.equal(callbacks, 0);
  await act(async () => { await button(app, 'Save').props.onPress(); });
  assert.match(text(app), /Saved\./);
  assert.equal(callbacks, 1);
  app.unmount();
});

test('repeated save presses remain single-flight until persistence settles', async () => {
  const pending = deferred();
  let saves = 0, app;
  const { SettingsScreen } = source('src/screens/SettingsScreen.tsx', mocks({
    loadSettings: async () => connection, saveSettings: () => { saves++; return pending.promise; },
  }));
  await act(async () => { app = create(React.createElement(SettingsScreen, { onSaved() {} })); });
  const save = button(app, 'Save').props.onPress;
  let first;
  await act(async () => { first = save(); save(); });
  assert.equal(saves, 1);
  assert.equal(button(app, 'Saving…').props.disabled, true);
  await act(async () => { pending.resolve(); await first; });
  assert.equal(button(app, 'Save').props.disabled, false);
  app.unmount();
});

test('confirmation stays mounted during the post-save connection refresh', async () => {
  const refresh = deferred();
  let calls = 0, app;
  const App = source('App.tsx', mocks({
    loadSettings: async () => ++calls === 3 ? refresh.promise : connection,
    saveSettings: async () => {},
  })).default;
  await act(async () => { app = create(React.createElement(App)); });
  await act(async () => { tab(app, 'settings').props.onPress(); });
  await act(async () => { await button(app, 'Save').props.onPress(); });
  assert.match(text(app), /Saved\./);
  assert.equal(app.root.findAllByType('TextInput').length, 2);
  await act(async () => { refresh.resolve(newer); });
  assert.match(text(app), /Saved\./);
  app.unmount();
});

for (const failure of [false, true]) {
  test(`older post-save reload ${failure ? 'failure' : 'success'} cannot replace the newest connection`, async () => {
    const stale = deferred();
    let calls = 0, app;
    const App = source('App.tsx', mocks({
      loadSettings: async () => ++calls === 3 ? stale.promise : calls === 4 ? newer : connection,
      saveSettings: async () => {},
    })).default;
    await act(async () => { app = create(React.createElement(App)); });
    await act(async () => { tab(app, 'settings').props.onPress(); });
    await act(async () => { await button(app, 'Save').props.onPress(); });
    await act(async () => { app.root.findAllByType('TextInput')[0].props.onChangeText(newer.baseUrl); });
    await act(async () => { await button(app, 'Save').props.onPress(); });
    await act(async () => { tab(app, 'runs').props.onPress(); });
    assert.equal(app.root.findByType('RunsListScreen').props.settings, newer);
    await act(async () => { failure ? stale.reject(new Error('obsolete read failure')) : stale.resolve(connection); });
    assert.doesNotMatch(text(app), /Could not load connection settings/);
    assert.equal(app.root.findByType('RunsListScreen').props.settings, newer);
    app.unmount();
  });
}

test('callbacks retained after App unmount cannot start another settings read', async () => {
  let calls = 0, app;
  const App = source('App.tsx', mocks({ loadSettings: async () => { calls++; return connection; } })).default;
  await act(async () => { app = create(React.createElement(App)); });
  await act(async () => { tab(app, 'settings').props.onPress(); });
  const onSaved = app.root.find(node => node.type.name === 'SettingsScreen').props.onSaved;
  await act(async () => { app.unmount(); });
  const before = calls;
  await act(async () => { onSaved(); });
  assert.equal(calls, before);
});

test('a save finishing after leaving Settings updates the still-mounted App', async () => {
  const saved = deferred();
  let current = connection, app, operation;
  const App = source('App.tsx', mocks({
    loadSettings: async () => current,
    saveSettings: async () => { await saved.promise; current = newer; },
  })).default;
  await act(async () => { app = create(React.createElement(App)); });
  await act(async () => { tab(app, 'settings').props.onPress(); });
  await act(async () => { operation = button(app, 'Save').props.onPress(); });
  await act(async () => { tab(app, 'runs').props.onPress(); });
  await act(async () => { saved.resolve(); await operation; });
  assert.equal(app.root.findByType('RunsListScreen').props.settings, newer);
  app.unmount();
});
