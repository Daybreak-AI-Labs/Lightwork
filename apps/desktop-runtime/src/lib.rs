//! Fixed local dashboard launch and bounded, in-memory startup diagnostics.
//! Both desktop entry points use the same installation lookup and retry rules.

use std::ffi::OsString;
use std::io::Read;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub const DASHBOARD_URL: &str = "http://127.0.0.1:8765/";
const MAX_LOG_BYTES: usize = 32 * 1024;

type Logs = Arc<Mutex<String>>;

#[derive(Default)]
struct Control {
    child: Option<Child>,
    logs: Logs,
    binary: Option<PathBuf>,
    recovery: bool,
}

#[derive(Default)]
pub struct Dashboard(Mutex<Control>);

pub fn listening() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], 8765));
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

fn record(logs: &Logs, text: &str) {
    if let Ok(mut log) = logs.lock() {
        log.push_str(text);
        if log.len() > MAX_LOG_BYTES {
            let mut start = log.len() - MAX_LOG_BYTES;
            while !log.is_char_boundary(start) {
                start += 1;
            }
            log.drain(..start);
        }
    }
}

fn capture(mut stream: impl Read + Send + 'static, logs: Logs) {
    std::thread::spawn(move || {
        let mut bytes = [0u8; 2048];
        loop {
            match stream.read(&mut bytes) {
                Ok(0) => break,
                Ok(size) => record(&logs, &String::from_utf8_lossy(&bytes[..size])),
                Err(error) => {
                    record(
                        &logs,
                        &format!("\nCould not read startup output: {error}\n"),
                    );
                    break;
                }
            }
        }
    });
}

/// Check installer locations explicitly because GUI apps may have an old PATH.
fn maverick_bin() -> OsString {
    let exe = if cfg!(windows) {
        "maverick.exe"
    } else {
        "maverick"
    };
    if let Some(explicit) = std::env::var_os("MAVERICK_BIN") {
        return explicit;
    }
    let mut candidates = Vec::<PathBuf>::new();
    if let Some(dir) = std::env::var_os("PIPX_BIN_DIR") {
        candidates.push(PathBuf::from(dir).join(exe));
    }
    for var in ["HOME", "USERPROFILE"] {
        if let Some(home) = std::env::var_os(var) {
            let home = PathBuf::from(home);
            candidates.push(home.join(".local").join("bin").join(exe));
            candidates.push(home.join("bin").join(exe));
        }
    }
    for dir in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"] {
        candidates.push(Path::new(dir).join(exe));
    }
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .map(PathBuf::into_os_string)
        .unwrap_or_else(|| exe.into())
}

fn spawn_dashboard(logs: &Logs, binary: Option<&Path>, recovery: bool) -> Result<Child, String> {
    let mut command = Command::new(
        binary
            .map(|path| path.as_os_str().to_owned())
            .unwrap_or_else(maverick_bin),
    );
    command
        .args(["dashboard", "--host", "127.0.0.1", "--port", "8765"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if recovery {
        command.arg("--recovery-mode");
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = command.spawn().map_err(|error| {
        let message = format!("Lightwork could not start: {error}");
        record(logs, &format!("{message}\n"));
        message
    })?;
    if let Some(stdout) = child.stdout.take() {
        capture(stdout, Arc::clone(logs));
    }
    if let Some(stderr) = child.stderr.take() {
        capture(stderr, Arc::clone(logs));
    }
    Ok(child)
}

fn reap(control: &mut Control) -> Result<(), String> {
    if let Some(child) = control.child.as_mut() {
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            record(&control.logs, &format!("\nLightwork exited: {status}\n"));
            control.child = None;
        }
    }
    Ok(())
}

fn ensure_started(
    control: &mut Control,
    is_listening: impl FnOnce() -> bool,
    spawn: impl FnOnce(&Logs) -> Result<Child, String>,
) -> Result<String, String> {
    reap(control)?;
    if is_listening() {
        return Ok("Lightwork is responding on this computer.".into());
    }
    if control.child.is_some() {
        return Ok("Lightwork is still starting.".into());
    }
    // Old output readers retain the old buffer, so a retry cannot mix attempts.
    control.logs = Logs::default();
    record(&control.logs, "Starting Lightwork on this computer.\n");
    control.child = Some(spawn(&control.logs)?);
    Ok("Starting Lightwork…".into())
}

impl Dashboard {
    /// Customer packages pin their bundled runtime. A missing sidecar fails
    /// visibly rather than falling through to PATH or an environment override.
    pub fn with_binary(binary: PathBuf) -> Self {
        Self(Mutex::new(Control {
            binary: Some(binary),
            ..Control::default()
        }))
    }

    pub fn start(&self) -> Result<String, String> {
        let mut control = self.0.lock().map_err(|_| "Startup state is unavailable.")?;
        let binary = control.binary.clone();
        let recovery = control.recovery;
        ensure_started(&mut control, listening, |logs| {
            spawn_dashboard(logs, binary.as_deref(), recovery)
        })
    }

    /// Restart only this app's owned process into a fixed local mode. A
    /// separately managed dashboard is never stopped or reported as isolated.
    pub fn switch_recovery_mode(&self, recovery: bool) -> Result<String, String> {
        let mut control = self.0.lock().map_err(|_| "Startup state is unavailable.")?;
        reap(&mut control)?;
        may_switch_mode(control.child.is_some(), listening())?;
        stop_control(&mut control);
        if listening() {
            return Err("The previous workspace is still responding. Recovery has not started; retry after it closes.".into());
        }
        control.recovery = recovery;
        let binary = control.binary.clone();
        ensure_started(
            &mut control,
            || false,
            |logs| spawn_dashboard(logs, binary.as_deref(), recovery),
        )
    }

    pub fn diagnostics(&self) -> Result<String, String> {
        let mut control = self.0.lock().map_err(|_| "Startup state is unavailable.")?;
        reap(&mut control)?;
        let log = control
            .logs
            .lock()
            .map_err(|_| "Startup details are unavailable.")?;
        Ok(if log.is_empty() {
            "No startup output was recorded. Lightwork may already be running independently.".into()
        } else {
            log.clone()
        })
    }

    /// An independently running dashboard is never placed in our owned state.
    pub fn stop_owned(&self) {
        if let Ok(mut control) = self.0.lock() {
            stop_control(&mut control);
        }
    }

    /// Maintenance may pause only a process owned by this desktop instance.
    pub fn pause_for_maintenance(&self) -> Result<(), String> {
        let mut control = self.0.lock().map_err(|_| "Startup state is unavailable.")?;
        reap(&mut control)?;
        may_switch_mode(control.child.is_some(), listening())?;
        stop_control(&mut control);
        if listening() {
            return Err("Lightwork is still responding. Installation has not started; retry after it closes.".into());
        }
        Ok(())
    }
}

fn may_switch_mode(owned: bool, responding: bool) -> Result<(), String> {
    if responding && !owned {
        return Err("This workspace is managed outside the desktop app. Its administrator must coordinate recovery; no process was stopped.".into());
    }
    Ok(())
}

fn stop_control(control: &mut Control) {
    if let Some(mut child) = control.child.take() {
        // A frozen one-file runtime owns an extraction/worker child.
        // Kill only this still-live owned process tree before MSI
        // replaces its files; killing just the bootloader leaves the
        // dashboard and its file locks behind on Windows.
        #[cfg(windows)]
        if matches!(child.try_wait(), Ok(None)) {
            use std::os::windows::process::CommandExt;
            if let Some(system_root) = std::env::var_os("SystemRoot") {
                let _ = Command::new(PathBuf::from(system_root).join("System32/taskkill.exe"))
                    .args(["/PID", &child.id().to_string(), "/T", "/F"])
                    .creation_flags(0x0800_0000)
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

impl Drop for Dashboard {
    fn drop(&mut self) {
        self.stop_owned();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovery_cannot_stop_an_independently_managed_workspace() {
        assert!(may_switch_mode(false, true).is_err());
        assert!(may_switch_mode(false, false).is_ok());
        assert!(may_switch_mode(true, true).is_ok());
    }

    #[test]
    fn missing_customer_runtime_cannot_fall_back_to_an_installed_cli() {
        let missing = std::env::temp_dir().join("lightwork-no-such-customer-runtime.exe");
        let logs = Logs::default();
        assert!(spawn_dashboard(&logs, Some(&missing), false).is_err());
        assert!(logs.lock().unwrap().contains("could not start"));
    }

    #[test]
    fn customer_runtime_path_remains_pinned_across_retries() {
        let path = PathBuf::from("customer-runtime/maverick.exe");
        let dashboard = Dashboard::with_binary(path.clone());
        let mut control = dashboard.0.lock().unwrap();
        for _ in 0..2 {
            let _ = ensure_started(&mut control, || false, |_| Err("missing".into()));
            assert_eq!(control.binary.as_ref(), Some(&path));
        }
    }

    #[test]
    fn child_fixture() {
        if std::env::var_os("LIGHTWORK_RUNTIME_TEST_CHILD").is_some() {
            std::thread::sleep(Duration::from_secs(30));
        }
    }

    fn fixture_child(running: bool) -> Child {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", "tests::child_fixture"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        if running {
            command.env("LIGHTWORK_RUNTIME_TEST_CHILD", "1");
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        command.spawn().unwrap()
    }

    #[test]
    fn running_owned_child_is_not_started_twice() {
        let dashboard = Dashboard::default();
        {
            let mut control = dashboard.0.lock().unwrap();
            ensure_started(&mut control, || false, |_| Ok(fixture_child(true))).unwrap();
            let pid = control.child.as_ref().unwrap().id();
            ensure_started(&mut control, || false, |_| panic!("duplicate launch")).unwrap();
            assert_eq!(control.child.as_ref().unwrap().id(), pid);
        }
        dashboard.stop_owned();
        assert!(dashboard.0.lock().unwrap().child.is_none());
    }

    #[test]
    fn exited_child_can_be_retried() {
        let mut control = Control::default();
        ensure_started(&mut control, || false, |_| Ok(fixture_child(false))).unwrap();
        control.child.as_mut().unwrap().wait().unwrap();
        let mut attempted = false;
        let result = ensure_started(
            &mut control,
            || false,
            |_| {
                attempted = true;
                Err("synthetic retry".into())
            },
        );
        assert!(attempted);
        assert!(result.is_err());
        assert!(control.child.is_none());
    }

    #[test]
    fn listening_dashboard_is_reused_without_spawning() {
        let mut control = Control::default();
        assert!(ensure_started(&mut control, || true, |_| panic!("must not launch")).is_ok());
        assert!(control.child.is_none());
    }

    #[test]
    fn failed_launch_is_retryable_and_keeps_diagnostics() {
        let mut control = Control::default();
        for _ in 0..2 {
            let result = ensure_started(
                &mut control,
                || false,
                |logs| {
                    record(logs, "Synthetic launch failure");
                    Err("Synthetic launch failure".into())
                },
            );
            assert!(result.is_err());
            assert!(control.child.is_none());
            assert!(control
                .logs
                .lock()
                .unwrap()
                .contains("Synthetic launch failure"));
        }
    }

    #[test]
    fn output_is_bounded_and_valid_utf8() {
        let logs = Logs::default();
        for _ in 0..100 {
            record(&logs, &"🔎".repeat(1000));
        }
        let log = logs.lock().unwrap();
        assert!(log.len() <= MAX_LOG_BYTES);
        assert!(log.ends_with('🔎'));
    }

    #[test]
    fn previous_attempt_cannot_write_into_retry_diagnostics() {
        let mut control = Control::default();
        let old_logs = Arc::clone(&control.logs);
        let _ = ensure_started(&mut control, || false, |_| Err("retry".into()));
        record(&old_logs, "late previous attempt");
        assert!(!control.logs.lock().unwrap().contains("previous attempt"));
    }
}
