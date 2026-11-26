//! A record can be read from a directory on disk or from a directory in the library. Verification treats both alike.

use std::cell::RefCell;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use crate::error::{At, Result, refuse};
use crate::files;
use crate::library::Library;

/// One entry that is not a directory, with the reason it cannot be read as a file, if any.
pub struct Member {
    pub name: String,
    pub problem: Option<String>,
}

pub trait Tree {
    /// How to name this tree to the producer.
    fn label(&self) -> String;
    /// Every entry that is not a directory: a directory's own entries in name order, then each directory below it.
    fn members(&self) -> Result<Vec<Member>>;
    /// A whole member. Meant for the small JSON and text members of a record.
    fn read(&self, name: &str) -> Result<Vec<u8>>;
    /// The SHA-256 and length of a member's bytes, read in bounded pieces.
    fn measure(&self, name: &str) -> Result<(String, u64)>;
}

pub struct Directory(pub PathBuf);

/// Every entry below a directory on disk that is not itself a directory.
pub fn entries(root: &Path) -> Result<Vec<(PathBuf, Option<String>)>> {
    files::safe_directory(root)?;
    let mut found = Vec::new();
    let mut unreadable = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let listing = match fs::read_dir(&directory) {
            Ok(listing) => listing,
            Err(error) => {
                unreadable.push((directory, Some(error.to_string())));
                continue;
            }
        };
        let mut paths: Vec<PathBuf> = listing.flatten().map(|entry| entry.path()).collect();
        paths.sort();
        let mut below = Vec::new();
        for path in paths {
            match path.symlink_metadata() {
                Ok(metadata) if metadata.is_dir() => below.push(path),
                Ok(metadata) if metadata.is_file() => found.push((path, None)),
                Ok(_) => found.push((path, Some("symbolic link or special file".to_string()))),
                Err(error) => found.push((path, Some(error.to_string()))),
            }
        }
        pending.extend(below.into_iter().rev());
    }
    found.extend(unreadable);
    Ok(found)
}

pub fn relative(path: &Path, root: &Path) -> String {
    files::text(path.strip_prefix(root).unwrap_or(path))
}

impl Tree for Directory {
    fn label(&self) -> String {
        files::text(&self.0)
    }

    fn members(&self) -> Result<Vec<Member>> {
        let found = entries(&self.0)?;
        Ok(found.into_iter().map(|(path, problem)| Member { name: relative(&path, &self.0), problem }).collect())
    }

    fn read(&self, name: &str) -> Result<Vec<u8>> {
        files::read_regular(&self.0.join(name))
    }

    fn measure(&self, name: &str) -> Result<(String, u64)> {
        let path = self.0.join(name);
        files::regular(&path)?;
        let size = fs::metadata(&path).at(&path)?.len();
        Ok((files::digest_reader(fs::File::open(&path).at(&path)?).at(&path)?, size))
    }
}

/// A directory in the library. Each stored content is digested once for as long as the shelf is used.
pub struct Shelf<'a> {
    pub library: &'a Library,
    pub node: i64,
    measured: RefCell<HashMap<i64, (String, u64)>>,
}

impl<'a> Shelf<'a> {
    pub fn new(library: &'a Library, node: i64) -> Shelf<'a> {
        Shelf { library, node, measured: RefCell::new(HashMap::new()) }
    }

    /// The SHA-256 and length of the bytes actually stored for a content.
    pub fn measure_content(&self, content: i64) -> Result<(String, u64)> {
        if let Some(known) = self.measured.borrow().get(&content) {
            return Ok(known.clone());
        }
        let found = self.library.measure(content)?;
        self.measured.borrow_mut().insert(content, found.clone());
        Ok(found)
    }
}

impl Tree for Shelf<'_> {
    fn label(&self) -> String {
        self.library.path(self.node).unwrap_or_default()
    }

    fn members(&self) -> Result<Vec<Member>> {
        let found = self.library.files(self.node)?;
        Ok(found.into_iter().map(|(name, _)| Member { name, problem: None }).collect())
    }

    fn read(&self, name: &str) -> Result<Vec<u8>> {
        self.library.read(self.node, name)
    }

    fn measure(&self, name: &str) -> Result<(String, u64)> {
        match self.library.expect(self.node, name)?.content {
            Some(content) => self.measure_content(content),
            None => refuse(format!("Expected a file in the library: {name}")),
        }
    }
}
