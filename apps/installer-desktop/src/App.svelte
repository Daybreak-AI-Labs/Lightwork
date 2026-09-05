<script lang="ts">
  import { invoke } from '@tauri-apps/api/core';
  import { listen, type UnlistenFn } from '@tauri-apps/api/event';
  import { getCurrentWindow } from '@tauri-apps/api/window';
  import { onMount, onDestroy } from 'svelte';

  type Status = 'idle' | 'installing' | 'done' | 'failed' | 'cancelled';
  type InstallFailure = { code: string; message: string };

  const MAX_LOG_LINES = 1000;
  const MAX_LOG_CHARS = 500_000;
  const LOG_BATCH_MS = 50;

  let status: Status = 'idle';
  let lines: string[] = [];
  let errorMsg = '';
  let openingLightwork = false;
  let launchError = '';
  let startupDetails = '';
  let lightworkOpened = false;
  let logEl: HTMLElement | undefined;
  let activeInstall: Promise<void> | undefined;
  let cancellationRequested = false;
  let destroyed = false;
  let listenersRegistered = false;
  let listenerRegistrationFailed = false;
  let cleanupBlocked = false;
  let resolveListenerReadiness!: (ready: boolean) => void;
  const listenerReadiness = new Promise<boolean>((resolve) => {
    resolveListenerReadiness = resolve;
  });
  let pendingLogLines: string[] = [];
  let logFlushTimer: ReturnType<typeof setTimeout> | undefined;

  const unlisteners: UnlistenFn[] = [];

  function flushLogs() {
    if (logFlushTimer !== undefined) {
      clearTimeout(logFlushTimer);
      logFlushTimer = undefined;
    }
    if (pendingLogLines.length === 0) return;

    let next = lines.concat(pendingLogLines);
    pendingLogLines = [];
    if (next.length > MAX_LOG_LINES) {
      next = next.slice(next.length - MAX_LOG_LINES);
    }
    let chars = 0;
    let first = next.length;
    while (first > 0) {
      const size = next[first - 1].length + 1;
      if (chars + size > MAX_LOG_CHARS) break;
      chars += size;
      first -= 1;
    }
    lines = first === 0 ? next : next.slice(first);
    queueMicrotask(() => logEl?.scrollTo(0, logEl.scrollHeight));
  }

  function queueLog(line: string) {
    pendingLogLines.push(line);
    if (pendingLogLines.length >= 50) {
      flushLogs();
    } else if (logFlushTimer === undefined) {
      logFlushTimer = setTimeout(flushLogs, LOG_BATCH_MS);
    }
  }

  function installFailure(error: unknown): InstallFailure {
    if (typeof error === 'object' && error !== null) {
      const value = error as { code?: unknown; message?: unknown };
      if (typeof value.code === 'string' && typeof value.message === 'string') {
        return { code: value.code, message: value.message };
      }
    }
    return { code: 'failed', message: String(error) };
  }

  async function registerListener<T>(
    event: string,
    handler: Parameters<typeof listen<T>>[1]
  ) {
    const unlisten = await listen<T>(event, handler);
    if (destroyed) {
      unlisten();
    } else {
      unlisteners.push(unlisten);
    }
  }

  onMount(() => {
    void Promise.all([
      registerListener<string>('install-log', (e) => {
        queueLog(e.payload);
      }),
      registerListener<void>('install-close-requested', () => {
        void confirmCancelAndClose();
      }),
      registerListener<void>('install-cleanup-failed-close-requested', () => {
        void confirmForceExit();
      }),
    ]).then(
      () => {
        listenersRegistered = true;
        resolveListenerReadiness(true);
      },
      (error) => {
        listenerRegistrationFailed = true;
        errorMsg = `Could not initialize installer event listeners: ${String(error)}`;
        status = 'failed';
        resolveListenerReadiness(false);
      }
    );
  });

  onDestroy(() => {
    destroyed = true;
    if (logFlushTimer !== undefined) clearTimeout(logFlushTimer);
    unlisteners.splice(0).forEach((unlisten) => unlisten());
  });

  async function startInstall() {
    if (!(await listenerReadiness)) return;
    status = 'installing';
    lines = [];
    pendingLogLines = [];
    if (logFlushTimer !== undefined) {
      clearTimeout(logFlushTimer);
      logFlushTimer = undefined;
    }
    errorMsg = '';
    cleanupBlocked = false;
    cancellationRequested = false;
    const operation = invoke<void>('install');
    activeInstall = operation;
    try {
      await operation;
      flushLogs();
      // The command response is the authoritative terminal signal. Progress
      // events are intentionally best-effort, so a lost event must never
      // leave a completed installation stuck on this screen.
      status = 'done';
    } catch (e) {
      flushLogs();
      const failure = installFailure(e);
      if (cancellationRequested && failure.code === 'cancelled') {
        status = 'cancelled';
        errorMsg = failure.message;
      } else {
        status = 'failed';
        cleanupBlocked = failure.code === 'cleanup_failed';
        errorMsg = failure.message;
      }
    } finally {
      if (activeInstall === operation) activeInstall = undefined;
    }
  }

  async function cancelInstall(): Promise<boolean> {
    if (status !== 'installing' || cancellationRequested) return false;
    cancellationRequested = true;
    const operation = activeInstall;
    try {
      await invoke('cancel_install');
    } catch (e) {
      cancellationRequested = false;
      errorMsg = `Could not cancel safely: ${installFailure(e).message}`;
      return false;
    }
    try {
      await operation;
      return false;
    } catch (error) {
      // startInstall presents the result; this return value only decides
      // whether it is safe for the close-request handler to close the window.
      return installFailure(error).code === 'cancelled';
    }
  }

  async function confirmCancelAndClose() {
    if (status !== 'installing') {
      await getCurrentWindow().close();
      return;
    }
    if (!window.confirm(
      'Installation is still running. Stop the installation and its background tasks, then close Lightwork?'
    )) {
      return;
    }
    if (await cancelInstall()) {
      await getCurrentWindow().close();
    }
  }

  async function confirmForceExit() {
    if (!window.confirm(
      'Some installation tasks may still be running. Starting another install is blocked. Force the installer app to close now?'
    )) {
      return;
    }
    try {
      await invoke('force_exit_after_cleanup_failure');
    } catch (error) {
      errorMsg = `Could not force the installer to exit: ${installFailure(error).message}`;
    }
  }

  async function openLightwork() {
    if (openingLightwork) return;
    openingLightwork = true;
    launchError = '';
    try {
      await invoke('open_lightwork');
      if (!destroyed) lightworkOpened = true;
    } catch (error) {
      if (!destroyed) {
        launchError = 'Lightwork could not open yet. Review startup details, then try again.';
        startupDetails = String(error);
      }
    } finally {
      if (!destroyed) openingLightwork = false;
    }
  }

  async function refreshStartupDetails() {
    try {
      const details = await invoke<string>('dashboard_diagnostics');
      if (!destroyed) startupDetails = details;
    } catch (error) {
      if (!destroyed) startupDetails = String(error);
    }
  }
</script>

<main>
  <p class="sr-only" aria-live="polite">
    {status === 'installing' ? (cancellationRequested ? 'Cancelling installation' : 'Installing Lightwork') : ''}
  </p>
  <header>
    <h1>Lightwork</h1>
    <p class="sub">Set up Lightwork on this computer.</p>
  </header>

  {#if status === 'idle'}
    <section>
      <p>
        You need an internet connection and authorized access to Lightwork's
        private GitHub repository. Python will be installed if needed.
      </p>
      <details><summary>What will be installed?</summary>
        <p>This installer downloads a pinned version of Lightwork's source code
          and installs it on this computer.</p>
      </details>
      <button disabled={!listenersRegistered} onclick={startInstall}>
        {listenersRegistered ? 'Install Lightwork' : 'Preparing installer…'}
      </button>
    </section>
  {:else if status === 'installing'}
    <section>
      <h2>Installing…</h2>
      <p class="sub">Hang tight — this can take a minute or two.</p>
      {#if errorMsg}<p class="error" role="alert">Installation could not be stopped safely. It is still running. You can try cancelling again.</p>{/if}
      <details><summary>Installation details</summary>
        {#if errorMsg}<p class="error">{errorMsg}</p>{/if}
        <pre bind:this={logEl} class="log">{lines.join('\n')}</pre>
      </details>
      <button class="cancel" disabled={cancellationRequested} onclick={cancelInstall}>
        {cancellationRequested ? 'Cancelling…' : 'Cancel installation'}
      </button>
    </section>
  {:else if status === 'done'}
    <section class="done">
      <h2>Lightwork is installed. 🎉</h2>
      <p>Open Lightwork to connect your AI service and finish setup in the app.</p>
      <button disabled={openingLightwork} onclick={openLightwork}>
        {openingLightwork ? 'Opening Lightwork…' : lightworkOpened ? 'Return to Lightwork' : 'Open Lightwork'}
      </button>
      {#if lightworkOpened}<p role="status">Lightwork opened in its own app window.</p>{/if}
      {#if launchError}<p class="error" role="alert">{launchError}</p>{/if}
      <details><summary>Startup details</summary>
        <button onclick={refreshStartupDetails}>Refresh startup details</button>
        <pre class="log">{startupDetails || 'No startup details loaded yet.'}</pre>
      </details>
      <details><summary>Installation details</summary><pre class="log">{lines.join('\n')}</pre></details>
    </section>
  {:else if status === 'failed'}
    <section class="failed">
      <h2>Install failed</h2>
      <p class="error" role="alert">{cleanupBlocked
        ? 'Some installation tasks may still be running. Starting another install is blocked.'
        : listenerRegistrationFailed ? 'The installer could not start. Reload it and try again.'
        : 'Installation could not finish. Review the details, then try again.'}</p>
      <details><summary>Installation details</summary>
        <p class="error">{errorMsg}</p><pre class="log">{lines.join('\n')}</pre>
      </details>
      {#if cleanupBlocked}
        <p>
          Review the installation details before closing the app. Force closing
          the installer does not confirm that its background tasks have stopped.
        </p>
        <button class="cancel" onclick={confirmForceExit}>Force close installer</button>
      {:else if listenerRegistrationFailed}
        <button onclick={() => window.location.reload()}>Reload installer</button>
      {:else}
        <button onclick={startInstall}>Try again</button>
      {/if}
    </section>
  {:else}
    <section class="cancelled">
      <h2>Installation cancelled</h2>
      <p>You can start again when you're ready.</p>
      <details><summary>Installation details</summary>
        <p>{errorMsg}</p><pre class="log">{lines.join('\n')}</pre>
      </details>
      <button onclick={startInstall}>Start again</button>
    </section>
  {/if}
</main>

<style>
  :global(body) {
    margin: 0;
    font-family: -apple-system, system-ui, sans-serif;
    background: #0d1117;
    color: #f0f6fc;
  }
  main { padding: 2rem; max-width: 720px; margin: 0 auto; }
  header { margin-bottom: 2rem; }
  h1 { font-size: 2rem; margin: 0; }
  .sub { color: #8b949e; }
  details { margin: 1rem 0; }
  summary { cursor: pointer; color: #c9d1d9; }
  summary:focus-visible { outline: 2px solid #58a6ff; outline-offset: 4px; }
  section { background: #161b22; padding: 1.5rem; border-radius: 8px; }
  h2 { margin-top: 0; font-size: 1.25rem; }
  button {
    background: #238636; color: #fff; border: none; padding: 0.7rem 1.2rem;
    border-radius: 6px; cursor: pointer; font-size: 1rem;
  }
  button:hover { background: #2ea043; }
  button:disabled {
    background: #30363d;
    cursor: wait;
    opacity: 0.75;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
  button.cancel {
    margin-top: 1rem;
    background: #8b1a1a;
  }
  button.cancel:hover { background: #a52727; }
  .log {
    margin-top: 1rem; max-height: 320px; overflow: auto;
    background: #010409; border: 1px solid #30363d; border-radius: 6px;
    padding: 0.75rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.8rem; line-height: 1.4; white-space: pre-wrap; word-break: break-word;
    color: #c9d1d9;
  }
  .done h2 { color: #2ea043; }
  .failed h2 { color: #f85149; }
  .cancelled h2 { color: #d29922; }
  .error { color: #f85149; }
</style>
