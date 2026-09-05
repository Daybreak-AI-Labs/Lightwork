//! Tauri shell for the local Lightwork dashboard.
//!
//! The window first shows a bundled splash page (ui/index.html + ui/app.js)
//! that polls `http://127.0.0.1:8765/healthz` (an auth-exempt endpoint, see
//! maverick_dashboard/app.py `_AUTH_EXEMPT`) and navigates to the dashboard as
//! soon as the port answers. On the Rust side, if nothing is listening on
//! 127.0.0.1:8765 at launch we spawn the user's installed CLI —
//! `maverick dashboard --host 127.0.0.1 --port 8765` — as a child process and
//! kill it again on exit. A dashboard the user already runs is left untouched
//! (we only ever kill a child we spawned).
//!
//! Why spawn the installed CLI instead of bundling a true Tauri "sidecar"
//! binary: the dashboard is a Python process; bundling it would mean shipping a
//! Python runtime inside the app. The CLI installers (apps/installer-desktop,
//! apps/installer-msi, pipx) already own "how Lightwork gets installed"; this
//! shell stays a thin window.

use lightwork_desktop_runtime::{Dashboard, DASHBOARD_URL};
use std::process::Command;

use tauri::menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::{Manager, RunEvent};

#[tauri::command]
fn retry_dashboard(state: tauri::State<'_, Dashboard>) -> Result<String, String> {
    state.start()
}

#[tauri::command]
fn dashboard_diagnostics(state: tauri::State<'_, Dashboard>) -> Result<String, String> {
    state.diagnostics()
}

fn bundled_splash_url() -> tauri::Url {
    // Tauri's bundled-asset origin, matching the default useHttpsScheme=false
    // in tauri.conf.json. This destination never comes from dashboard content.
    let origin = if cfg!(windows) {
        "http://tauri.localhost/index.html"
    } else {
        "tauri://localhost/index.html"
    };
    origin.parse().expect("fixed bundled splash address")
}

fn reconnect_dashboard<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    start: impl FnOnce(),
) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window("main") {
        // Return to local recovery controls even if launch fails. Remote
        // dashboard pages retain no authority to invoke native commands.
        window.navigate(bundled_splash_url())?;
        start();
    }
    Ok(())
}

/// Open a URL in the user's default browser (std only — no extra plugin/dep).
fn open_in_browser(url: &str) {
    let spawn = |program: &str, args: &[&str]| {
        let _ = Command::new(program).args(args).spawn();
    };
    #[cfg(target_os = "macos")]
    spawn("open", &[url]);
    #[cfg(target_os = "windows")]
    spawn("cmd", &["/C", "start", "", url]);
    #[cfg(all(unix, not(target_os = "macos")))]
    spawn("xdg-open", &[url]);
}

pub fn run() {
    let dashboard = Dashboard::default();
    let _ = dashboard.start();

    tauri::Builder::default()
        .manage(dashboard)
        .invoke_handler(tauri::generate_handler![
            retry_dashboard,
            dashboard_diagnostics
        ])
        // A real menu: Reload / Open in Browser / Quit, plus a standard Edit
        // menu so Cmd-C / Cmd-V / Select-All work in the dashboard's text
        // fields (on macOS the webview has no clipboard shortcuts without it).
        .menu(|handle| {
            let open_browser = MenuItemBuilder::with_id("open_browser", "Open in Browser")
                .accelerator("CmdOrCtrl+Shift+O")
                .build(handle)?;
            let reload = MenuItemBuilder::with_id("reload", "Reload Dashboard")
                .accelerator("CmdOrCtrl+R")
                .build(handle)?;
            let reconnect =
                MenuItemBuilder::with_id("reconnect", "Reconnect Lightwork").build(handle)?;
            let quit = PredefinedMenuItem::quit(handle, Some("Quit Lightwork"))?;
            let app_menu = SubmenuBuilder::new(handle, "Lightwork")
                .item(&open_browser)
                .item(&reload)
                .item(&reconnect)
                .separator()
                .item(&quit)
                .build()?;
            let edit = SubmenuBuilder::new(handle, "Edit")
                .undo()
                .redo()
                .separator()
                .cut()
                .copy()
                .paste()
                .select_all()
                .build()?;
            MenuBuilder::new(handle).item(&app_menu).item(&edit).build()
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            "reload" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.eval("window.location.reload()");
                }
            }
            "open_browser" => open_in_browser(DASHBOARD_URL),
            "reconnect" => {
                let _ = reconnect_dashboard(app, || {
                    let _ = app.state::<Dashboard>().start();
                });
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("error while building the Lightwork desktop shell")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                // Kill only the dashboard *we* started; an externally started
                // one was never put into the Sidecar state.
                app.state::<Dashboard>().stop_owned();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reconnect_returns_a_failed_dashboard_to_bundled_controls_before_launch() {
        let app = tauri::test::mock_app();
        let window = tauri::WebviewWindowBuilder::new(
            &app,
            "main",
            tauri::WebviewUrl::External(DASHBOARD_URL.parse().unwrap()),
        )
        .build()
        .unwrap();
        let mut starts = 0;
        reconnect_dashboard(app.handle(), || {
            assert_eq!(window.url().unwrap(), bundled_splash_url());
            starts += 1;
        })
        .unwrap();
        assert_eq!(starts, 1);
        assert_ne!(window.url().unwrap().as_str(), DASHBOARD_URL);
    }

    #[test]
    fn reconnect_does_not_launch_without_a_recovery_window() {
        let app = tauri::test::mock_app();
        reconnect_dashboard(app.handle(), || panic!("no window to recover")).unwrap();
    }
}
