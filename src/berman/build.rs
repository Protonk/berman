//! Fix the identity of the sources in the executable: a digest over every file the build reads.

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use sha2::{Digest, Sha256};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn collect(directory: &Path, members: &mut BTreeMap<String, String>) {
    let mut entries: Vec<_> = fs::read_dir(directory).expect("source directory").flatten().collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            collect(&path, members);
        } else if path.extension().is_some_and(|extension| extension == "rs" || extension == "md") {
            add(&path, members);
        }
    }
}

fn add(path: &Path, members: &mut BTreeMap<String, String>) {
    println!("cargo:rerun-if-changed={}", path.display());
    let name = path.to_string_lossy().replace('\\', "/");
    members.insert(name, hex(&Sha256::digest(fs::read(path).expect("source file"))));
}

fn main() {
    let mut members = BTreeMap::new();
    for name in ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml"] {
        add(Path::new(name), &mut members);
    }
    collect(Path::new("src/berman"), &mut members);
    println!("cargo:rerun-if-changed=src/berman");
    // The same spelling the records use for a digest of JSON: sorted keys and spaced separators.
    let listing: Vec<String> = members.iter().map(|(name, digest)| format!("\"{name}\": \"{digest}\"")).collect();
    let source = hex(&Sha256::digest(format!("{{{}}}", listing.join(", "))));
    println!("cargo:rustc-env=BERMAN_SOURCE_SHA256={source}");
    println!("cargo:rustc-env=BERMAN_TARGET={}", std::env::var("TARGET").expect("target triple"));
    println!("cargo:rerun-if-env-changed=BERMAN_COMMIT");
}
