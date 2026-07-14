use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=MAVERICK_INSTALL_REF");
    println!("cargo:rerun-if-changed=../../../deploy/desktop/install.sh");
    println!("cargo:rerun-if-changed=../../../deploy/desktop/install.ps1");

    let install_ref = std::env::var("MAVERICK_INSTALL_REF")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(current_git_commit);
    let install_ref = validate_install_ref(&install_ref);
    println!("cargo:rustc-env=MAVERICK_INSTALL_REF={install_ref}");

    tauri_build::build();
}

fn validate_install_ref(value: &str) -> String {
    let trimmed = value.trim();
    let valid = trimmed.len() == 40
        && trimmed
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
    if !valid {
        panic!(
            "MAVERICK_INSTALL_REF must be a lowercase, full 40-character commit SHA"
        );
    }
    trimmed.to_owned()
}

fn current_git_commit() -> String {
    let output = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .output()
        .expect("MAVERICK_INSTALL_REF is not set and git is unavailable");

    if !output.status.success() {
        panic!(
            "MAVERICK_INSTALL_REF is not set and git rev-parse HEAD failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    String::from_utf8(output.stdout)
        .expect("git rev-parse HEAD did not emit UTF-8")
        .trim()
        .to_owned()
}
