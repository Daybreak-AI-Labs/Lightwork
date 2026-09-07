fn main() {
    tauri_build::build();
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        // Tauri embeds resources for the application binary; the lib unit-test
        // harness also imports TaskDialogIndirect and needs Common Controls v6.
        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'"); // pragma: allowlist secret -- Microsoft's public assembly identity

        // The application already has Tauri's full manifest in resource.lib.
        // Disable linker synthesis for that binary so both manifests cannot
        // collide; its existing embedded declarations remain intact.
        println!("cargo:rustc-link-arg-bin=maverick-desktop=/MANIFEST:NO");
    }
}
