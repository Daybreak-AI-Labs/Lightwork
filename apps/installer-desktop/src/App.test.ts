import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App.svelte';

const tauri = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  unlisten: vi.fn(),
  close: vi.fn(),
}));

vi.mock('@tauri-apps/api/core', () => ({ invoke: tauri.invoke }));
vi.mock('@tauri-apps/api/event', () => ({ listen: tauri.listen }));
vi.mock('@tauri-apps/api/window', () => ({
  getCurrentWindow: () => ({ close: tauri.close }),
}));

describe('installer terminal state', () => {
  beforeEach(() => {
    tauri.invoke.mockReset();
    tauri.listen.mockReset();
    tauri.unlisten.mockReset();
    tauri.close.mockReset();
    tauri.listen.mockResolvedValue(tauri.unlisten);
    tauri.close.mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('explains source installation and access requirements before install', async () => {
    render(App);
    const disclosure = screen.getByText(/downloads and installs Lightwork's source code/);
    expect(disclosure.closest('details')).toBeNull();
    expect(screen.getByText(/authorized access to the private Lightwork repository on GitHub/).closest('details')).toBeNull();
    expect(screen.getByText(/internet connection/)).toBeTruthy();
    expect((await screen.findByRole('button', { name: 'Install Lightwork' }) as HTMLButtonElement).disabled).toBe(false);
    expect(tauri.invoke).not.toHaveBeenCalled();
  });

  it('shows success when invoke resolves even if no completion event arrives', async () => {
    let completeInstall!: () => void;
    tauri.invoke.mockReturnValue(
      new Promise<void>((resolve) => {
        completeInstall = resolve;
      })
    );

    render(App);
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );

    expect(screen.getByRole('heading', { name: 'Installing…' })).toBeTruthy();
    expect(tauri.listen).toHaveBeenCalledWith('install-log', expect.any(Function));
    expect(tauri.listen).not.toHaveBeenCalledWith('install-done', expect.any(Function));

    completeInstall();

    expect(
      await screen.findByRole('heading', { name: /Lightwork is installed/ })
    ).toBeTruthy();
    expect(tauri.invoke).toHaveBeenCalledOnce();
    expect(tauri.invoke).toHaveBeenCalledWith('install');
  });

  it('shows the command error without depending on a failure event', async () => {
    tauri.invoke.mockRejectedValue({
      code: 'failed',
      message: 'authorization denied',
    });

    render(App);
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );

    expect(await screen.findByRole('heading', { name: 'Install failed' })).toBeTruthy();
    expect(screen.getByText('authorization denied')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).not.toContain('authorization denied');
    expect(screen.getByText('authorization denied').closest('details')?.open).toBe(false);
    await waitFor(() => {
      expect(tauri.listen).not.toHaveBeenCalledWith('install-failed', expect.any(Function));
    });
  });

  it('opens the installed app for setup and prevents repeated launch requests', async () => {
    let open!: () => void;
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return Promise.resolve();
      if (command === 'open_lightwork') return new Promise<void>((resolve) => { open = resolve; });
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    render(App);
    await fireEvent.click(await screen.findByRole('button', { name: 'Install Lightwork' }));
    await fireEvent.click(await screen.findByRole('button', { name: 'Open Lightwork' }));
    expect((screen.getByRole('button', { name: 'Opening Lightwork…' }) as HTMLButtonElement).disabled).toBe(true);
    expect(tauri.invoke).toHaveBeenNthCalledWith(2, 'open_lightwork');
    expect(screen.queryByText(/maverick init|Terminal or PowerShell/)).toBeNull();
    open();
    expect(await screen.findByRole('button', { name: 'Return to Lightwork' })).toBeTruthy();
    expect(screen.getByText('Lightwork opened in its own app window.')).toBeTruthy();
  });

  it('keeps launch retry and diagnostics inside the installer after startup fails', async () => {
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return Promise.resolve();
      if (command === 'open_lightwork') return Promise.reject('synthetic launch error');
      if (command === 'dashboard_diagnostics') return Promise.resolve('synthetic startup output');
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    render(App);
    await fireEvent.click(await screen.findByRole('button', { name: 'Install Lightwork' }));
    await fireEvent.click(await screen.findByRole('button', { name: 'Open Lightwork' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Review startup details');
    expect(screen.getByRole('alert').textContent).not.toContain('synthetic');
    expect((screen.getByRole('button', { name: 'Open Lightwork' }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText('synthetic launch error').closest('details')?.open).toBe(false);
    await fireEvent.click(screen.getByRole('button', { name: 'Refresh startup details' }));
    expect(await screen.findByText('synthetic startup output')).toBeTruthy();
  });

  it('waits for owned process teardown before reporting cancellation', async () => {
    let rejectInstall!: (reason: unknown) => void;
    const operation = new Promise<void>((_resolve, reject) => {
      rejectInstall = reject;
    });
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return operation;
      if (command === 'cancel_install') {
        rejectInstall({
          code: 'cancelled',
          message: 'owned process tree terminated',
        });
        return Promise.resolve();
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });

    render(App);
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Cancel installation' })
    );

    expect(
      await screen.findByRole('heading', { name: 'Installation cancelled' })
    ).toBeTruthy();
    expect(tauri.invoke).toHaveBeenNthCalledWith(1, 'install');
    expect(tauri.invoke).toHaveBeenNthCalledWith(2, 'cancel_install');
  });

  it('confirms close, cancels, and closes only after install cleanup', async () => {
    let closeHandler: (() => void) | undefined;
    let rejectInstall!: (reason: unknown) => void;
    const operation = new Promise<void>((_resolve, reject) => {
      rejectInstall = reject;
    });
    tauri.listen.mockImplementation(
      async (event: string, handler: () => void) => {
        if (event === 'install-close-requested') closeHandler = handler;
        return tauri.unlisten;
      }
    );
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return operation;
      if (command === 'cancel_install') {
        rejectInstall({ code: 'cancelled', message: 'cancelled' });
        return Promise.resolve();
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(App);
    await waitFor(() => expect(closeHandler).toBeTypeOf('function'));
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );
    closeHandler?.();

    await waitFor(() => expect(tauri.close).toHaveBeenCalledOnce());
    expect(tauri.invoke).toHaveBeenCalledWith('cancel_install');
  });

  it('does not close or claim cancellation when process teardown fails', async () => {
    let closeHandler: (() => void) | undefined;
    let rejectInstall!: (reason: unknown) => void;
    const operation = new Promise<void>((_resolve, reject) => {
      rejectInstall = reject;
    });
    tauri.listen.mockImplementation(
      async (event: string, handler: () => void) => {
        if (event === 'install-close-requested') closeHandler = handler;
        return tauri.unlisten;
      }
    );
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return operation;
      if (command === 'cancel_install') {
        rejectInstall({
          code: 'cleanup_failed',
          message: 'installer descendants survived forced teardown',
        });
        return Promise.resolve();
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(App);
    await waitFor(() => expect(closeHandler).toBeTypeOf('function'));
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );
    closeHandler?.();

    expect(await screen.findByRole('heading', { name: 'Install failed' })).toBeTruthy();
    expect(
      screen.getByText('installer descendants survived forced teardown')
    ).toBeTruthy();
    expect(tauri.close).not.toHaveBeenCalled();
  });

  it('unregisters listeners that resolve after component destruction', async () => {
    const lateUnlisten = vi.fn();
    const resolvers: Array<(unlisten: typeof lateUnlisten) => void> = [];
    tauri.listen.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        })
    );

    render(App);
    await waitFor(() => expect(resolvers).toHaveLength(3));
    cleanup();
    resolvers.forEach((resolve) => resolve(lateUnlisten));

    await waitFor(() => expect(lateUnlisten).toHaveBeenCalledTimes(3));
  });

  it('shows a cancellation request failure while the installer remains active', async () => {
    let completeInstall!: () => void;
    const operation = new Promise<void>((resolve) => {
      completeInstall = resolve;
    });
    tauri.invoke.mockImplementation((command: string) => {
      if (command === 'install') return operation;
      if (command === 'cancel_install') {
        return Promise.reject({ code: 'failed', message: 'process control unavailable' });
      }
      return Promise.reject(new Error(`unexpected command: ${command}`));
    });

    render(App);
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Install Lightwork' })
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Cancel installation' }));

    expect((await screen.findByRole('alert')).textContent).toContain(
      'Installation could not be stopped safely'
    );
    expect(screen.getByText('Could not cancel safely: process control unavailable').closest('details')?.open).toBe(false);
    expect(screen.getByRole('heading', { name: 'Installing…' })).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Cancel installation' }) as HTMLButtonElement).disabled).toBe(false);
    expect(tauri.close).not.toHaveBeenCalled();
    completeInstall();
    expect(await screen.findByRole('heading', { name: /Lightwork is installed/ })).toBeTruthy();
  });
});
