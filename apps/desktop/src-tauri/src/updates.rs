//! User-reviewed maintenance for the packaged Windows application.
//! The download address and verification key come only from the compiled
//! release configuration. Dashboard content cannot select an installer.

use serde::Serialize;
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(windows)]
use std::sync::Mutex;
use tauri::Manager;

#[derive(Default)]
pub struct Maintenance {
    busy: AtomicBool,
    #[cfg(windows)]
    pending: Mutex<Option<(String, tauri_plugin_updater::Update, bool)>>,
    #[cfg(windows)]
    sequence: Mutex<u64>,
}

struct Busy<'a>(&'a AtomicBool);
impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

fn acquire(state: &Maintenance) -> Result<Busy<'_>, String> {
    state
        .busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .map_err(|_| "Another maintenance operation is still running.")?;
    Ok(Busy(&state.busy))
}

fn local_controls(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window
        .url()
        .map_err(|_| "The maintenance window is unavailable.")?;
    let expected = super::bundled_page_url("maintenance.html");
    if url != expected {
        return Err("Open Install & updates from the Lightwork app menu.".into());
    }
    Ok(())
}

#[derive(Serialize)]
pub struct MaintenanceStatus {
    version: String,
    packaged_runtime: bool,
    updates_configured: bool,
    recovery: serde_json::Value,
}

fn channel_configured(app: &tauri::AppHandle) -> bool {
    let Some(config) = app.config().plugins.0.get("updater") else {
        return false;
    };
    let key_present = config
        .get("pubkey")
        .and_then(|v| v.as_str())
        .is_some_and(|key| !key.trim().is_empty());
    let endpoints_valid = config
        .get("endpoints")
        .and_then(|v| v.as_array())
        .is_some_and(|items| {
            !items.is_empty()
                && items.iter().all(|item| {
                    item.as_str()
                        .and_then(|text| tauri::Url::parse(text).ok())
                        .is_some_and(|url| {
                            url.scheme() == "https"
                                && url.host_str().is_some()
                                && url.username().is_empty()
                                && url.password().is_none()
                                && url.fragment().is_none()
                        })
                })
        });
    cfg!(all(windows, feature = "customer-runtime")) && key_present && endpoints_valid
}

#[tauri::command]
pub fn maintenance_status(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
) -> Result<MaintenanceStatus, String> {
    local_controls(&window)?;
    Ok(MaintenanceStatus {
        version: app.package_info().version.to_string(),
        packaged_runtime: cfg!(feature = "customer-runtime"),
        updates_configured: channel_configured(&app),
        recovery: if cfg!(feature = "customer-runtime") {
            protocol(&runtime_path()?, "status", &serde_json::json!({})).unwrap_or_else(|error|
                serde_json::json!({"recovery_points": [], "unavailable": true, "detail": error}))
        } else {
            serde_json::json!({"recovery_points": [], "incomplete": false})
        },
    })
}

#[derive(Serialize)]
pub struct ReviewedUpdate {
    review: String,
    version: String,
    current_version: String,
    notes: String,
    repair: bool,
    identity_transition: Option<serde_json::Value>,
}

#[tauri::command]
pub async fn check_desktop_update(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Maintenance>,
    repair: bool,
) -> Result<Option<ReviewedUpdate>, String> {
    local_controls(&window)?;
    let _busy = acquire(&state)?;
    if !channel_configured(&app) {
        return Err("This build has no verified customer update channel. Obtain a customer installer from your administrator.".into());
    }
    #[cfg(windows)]
    {
        use tauri_plugin_updater::UpdaterExt;
        *state
            .pending
            .lock()
            .map_err(|_| "Update state is unavailable.")? = None;
        let app_for_exit = app.clone();
        let mut builder = app
            .updater_builder()
            .timeout(std::time::Duration::from_secs(30))
            .on_before_exit(move || {
                app_for_exit
                    .state::<lightwork_desktop_runtime::Dashboard>()
                    .stop_owned();
            });
        if repair {
            builder = builder
                .endpoints(version_endpoints(
                    &app,
                    &app.package_info().version.to_string(),
                )?)
                .map_err(|e| e.to_string())?
                .version_comparator(|current, release| release.version == current)
                .installer_args(["REINSTALL=ALL", "REINSTALLMODE=amus"]);
        }
        let update = builder
            .build()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?;
        if let Some(update) = update {
            let transition = update
                .raw_json
                .get("identity_transition")
                .filter(|value| !value.is_null())
                .cloned();
            if transition
                .as_ref()
                .is_some_and(|value| repair || value.to_string().len() > 65536)
            {
                return Err("A signing identity transition requires a bounded, reviewed protected upgrade; it cannot use repair.".into());
            }
            // Signatures are mandatory, independently of HTTPS. Refuse unsafe
            // metadata URLs before any artifact download as defense in depth.
            if update.download_url.scheme() != "https"
                || !update.download_url.username().is_empty()
                || update.download_url.password().is_some()
            {
                return Err("The update download address is not an approved HTTPS address.".into());
            }
            let mut sequence = state
                .sequence
                .lock()
                .map_err(|_| "Update state is unavailable.")?;
            *sequence += 1;
            let review = sequence.to_string();
            let result = ReviewedUpdate {
                review: review.clone(),
                version: update.version.clone(),
                current_version: update.current_version.clone(),
                notes: update
                    .body
                    .clone()
                    .unwrap_or_default()
                    .chars()
                    .take(16_000)
                    .collect(),
                repair,
                identity_transition: transition,
            };
            *state
                .pending
                .lock()
                .map_err(|_| "Update state is unavailable.")? = Some((review, update, repair));
            return Ok(Some(result));
        }
        Ok(None)
    }
    #[cfg(not(windows))]
    {
        let _ = repair;
        Err("Customer maintenance is currently supported on Windows.".into())
    }
}

#[tauri::command]
pub async fn install_desktop_update(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Maintenance>,
    review: String,
    confirm: bool,
    authorization: Option<String>,
) -> Result<(), String> {
    local_controls(&window)?;
    let _busy = acquire(&state)?;
    if !confirm {
        return Err("Review and confirm the installation first.".into());
    }
    #[cfg(windows)]
    {
        let update = {
            let mut pending = state
                .pending
                .lock()
                .map_err(|_| "Update state is unavailable.")?;
            if pending.as_ref().map(|(id, _, _)| id.as_str()) != Some(review.as_str()) {
                return Err(
                    "This update review has expired. Check again before installing.".into(),
                );
            }
            pending.take().expect("checked pending update")
        };
        let (_, update, repair) = update;
        if !repair {
            use tauri_plugin_updater::UpdaterExt;
            let authorization =
                authorization.ok_or("Authorize this installation in Workspace recovery first.")?;
            let runtime = runtime_path()?;
            let details = protocol(
                &runtime,
                "inspect",
                &serde_json::json!({"authorization": authorization}),
            )?;
            if details["operation"] != "upgrade" || details["old_version"] != update.current_version
            {
                return Err("The authorization does not match the reviewed installation.".into());
            }
            let transition = update
                .raw_json
                .get("identity_transition")
                .cloned()
                .unwrap_or(serde_json::Value::Null);
            if details["identity_transition"] != transition {
                return Err("The offered signing identity transition differs from the administrator's exact review.".into());
            }
            let old = app.updater_builder()
                .endpoints(version_endpoints(&app, &update.current_version)?).map_err(|e| e.to_string())?
                .version_comparator(|current, release| release.version == current)
                .timeout(std::time::Duration::from_secs(30))
                .build().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?
                .ok_or("The exact installed release is unavailable. A protected upgrade requires its verified rollback installer.")?;
            let directory = std::path::PathBuf::from(
                details["directory"]
                    .as_str()
                    .ok_or("The private recovery directory is unavailable.")?,
            );
            let key = app
                .config()
                .plugins
                .0
                .get("updater")
                .and_then(|v| v.get("pubkey"))
                .and_then(|v| v.as_str())
                .ok_or("The release verification key is missing.")?;
            let mut metadata =
                serde_json::json!({"public_key": key, "identity_transition": transition});
            for (name, release) in [("old", &old), ("new", &update)] {
                if release.download_url.scheme() != "https"
                    || !release.download_url.username().is_empty()
                    || release.download_url.password().is_some()
                {
                    return Err("The retained installer address is not approved HTTPS.".into());
                }
                let bytes = download_bounded(release).await?;
                std::fs::write(directory.join(format!("{name}.msi")), bytes)
                    .map_err(|e| e.to_string())?;
                metadata[name] = serde_json::json!({"version": release.version, "signature": release.signature,
                    "runtime_sha256": release.raw_json.get("runtime_sha256")});
            }
            std::fs::write(
                directory.join("downloads.json"),
                serde_json::to_vec(&metadata).map_err(|e| e.to_string())?,
            )
            .map_err(|e| e.to_string())?;
            app.state::<lightwork_desktop_runtime::Dashboard>()
                .pause_for_maintenance()?;
            let prepared = protocol(
                &runtime,
                "prepare",
                &serde_json::json!({"authorization": authorization}),
            )?;
            launch_operation(&app, &prepared, &authorization)?;
            return Ok(());
        }
        // The plugin verifies the downloaded artifact's signature before it
        // runs the installer. Its Windows hook stops only our owned runtime.
        update
            .download_and_install(|_, _| {}, || {})
            .await
            .map_err(|e| e.to_string())
    }
    #[cfg(not(windows))]
    {
        let _ = (app, review, authorization);
        Err("Customer maintenance is currently supported on Windows.".into())
    }
}

#[tauri::command]
pub fn desktop_maintenance_logs(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Maintenance>,
    id: String,
) -> Result<serde_json::Value, String> {
    local_controls(&window)?;
    let _busy = acquire(&state)?;
    protocol(&runtime_path()?, "logs", &serde_json::json!({"id": id}))
}

fn runtime_path() -> Result<std::path::PathBuf, String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    Ok(executable
        .parent()
        .ok_or("The installed application directory is unavailable.")?
        .join("maverick.exe"))
}

#[cfg(windows)]
async fn download_bounded(update: &tauri_plugin_updater::Update) -> Result<Vec<u8>, String> {
    const LIMIT: usize = 1024 * 1024 * 1024;
    let (sender, receiver) = tokio::sync::oneshot::channel();
    let mut sender = Some(sender);
    let mut received = 0usize;
    let download = update.download(
        move |chunk, announced| {
            received = received.saturating_add(chunk);
            if received > LIMIT || announced.is_some_and(|size| size > LIMIT as u64) {
                if let Some(sender) = sender.take() {
                    let _ = sender.send(());
                }
            }
        },
        || {},
    );
    tokio::select! {
        biased;
        result = download => {
            let bytes = result.map_err(|e| e.to_string())?;
            if bytes.len() > LIMIT { Err("The installer exceeds the maintenance size limit.".into()) } else { Ok(bytes) }
        },
        _ = receiver => Err("The installer exceeds the maintenance size limit.".into()),
    }
}

fn protocol(
    binary: &std::path::Path,
    step: &str,
    payload: &serde_json::Value,
) -> Result<serde_json::Value, String> {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut command = Command::new(binary);
    command
        .args(["installation-maintenance", step])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    if let Some(mut input) = child.stdin.take() {
        input
            .write_all(&serde_json::to_vec(payload).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr)
            .chars()
            .take(16000)
            .collect());
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|_| "The maintenance response could not be verified.".into())
}

#[cfg(windows)]
fn version_endpoints(app: &tauri::AppHandle, version: &str) -> Result<Vec<tauri::Url>, String> {
    if version.is_empty() || !version.chars().all(|ch| ch.is_ascii_digit() || ch == '.') {
        return Err("The installed version has no immutable customer manifest.".into());
    }
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|v| v.get("endpoints"))
        .and_then(|v| v.as_array())
        .ok_or("The customer update channel is unavailable.".to_string())?
        .iter()
        .map(|value| {
            let base = tauri::Url::parse(
                value
                    .as_str()
                    .ok_or("The release address is unavailable.")?,
            )
            .map_err(|e| e.to_string())?;
            base.join(&format!("versions/{version}.json"))
                .map_err(|e| e.to_string())
        })
        .collect()
}

fn launch_operation(
    app: &tauri::AppHandle,
    prepared: &serde_json::Value,
    authorization: &str,
) -> Result<(), String> {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let helper = prepared["helper"]
        .as_str()
        .ok_or("The retained recovery helper is unavailable.")?;
    let mut command = Command::new(helper);
    command
        .args(["installation-maintenance", "execute"])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(&serde_json::to_vec(&serde_json::json!({"authorization": authorization, "parent_pid": std::process::id()})).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    }
    app.exit(0);
    Ok(())
}

#[tauri::command]
pub fn rollback_desktop_update(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Maintenance>,
    authorization: String,
    confirm: bool,
) -> Result<(), String> {
    local_controls(&window)?;
    let _busy = acquire(&state)?;
    if !confirm {
        return Err("Review and confirm the completed-upgrade rollback first.".into());
    }
    let runtime = runtime_path()?;
    let details = protocol(
        &runtime,
        "inspect",
        &serde_json::json!({"authorization": authorization}),
    )?;
    if details["operation"] != "rollback" {
        return Err("Use the rollback authorization from Workspace recovery.".into());
    }
    app.state::<lightwork_desktop_runtime::Dashboard>()
        .pause_for_maintenance()?;
    let prepared = protocol(
        &runtime,
        "prepare",
        &serde_json::json!({"authorization": authorization}),
    )?;
    launch_operation(&app, &prepared, &authorization)
}

#[tauri::command]
pub fn resume_desktop_maintenance(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Maintenance>,
    id: String,
    confirm: bool,
) -> Result<(), String> {
    local_controls(&window)?;
    let _busy = acquire(&state)?;
    if !confirm {
        return Err("Review and confirm resuming the unfinished operation first.".into());
    }
    app.state::<lightwork_desktop_runtime::Dashboard>()
        .pause_for_maintenance()?;
    let prepared = protocol(&runtime_path()?, "resume", &serde_json::json!({"id": id}))?;
    let authorization = prepared["authorization"]
        .as_str()
        .ok_or("The recovery authorization is unavailable.")?;
    launch_operation(&app, &prepared, authorization)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maintenance_does_not_overlap_checks_or_installation() {
        let state = Maintenance::default();
        let first = acquire(&state).unwrap();
        assert!(acquire(&state).is_err());
        drop(first);
        assert!(acquire(&state).is_ok());
    }
}
