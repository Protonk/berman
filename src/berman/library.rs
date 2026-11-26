//! The producer's library: directories and files kept in one encrypted SQLite database.
//!
//! Names, contents, project paths and indexes all live in encrypted pages, as do the journal and
//! write-ahead log. Temporary tables stay in memory. File contents are stored as bounded chunks
//! named by their digest, so copies and successive versions of a growing file share bytes, and
//! bytes stay for as long as any file refers to them. Reads stream to the caller.

use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

use rusqlite::{Connection, OptionalExtension, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::error::{At, Error, Result, refuse};
use crate::keys::{self, Store};
use crate::places::{self, Places};
use crate::{files, json};

pub const CHUNK: usize = 1024 * 1024;
const SCHEMA: &str = "berman.library/1";
pub const ROOT: i64 = 1;

const TABLES: &str = "
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE chunks(id INTEGER PRIMARY KEY, sha256 BLOB NOT NULL UNIQUE, data BLOB NOT NULL);
CREATE TABLE contents(id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, size INTEGER NOT NULL);
CREATE TABLE content_chunks(
    content INTEGER NOT NULL REFERENCES contents(id),
    seq INTEGER NOT NULL,
    chunk INTEGER NOT NULL REFERENCES chunks(id),
    PRIMARY KEY(content, seq)
) WITHOUT ROWID;
CREATE INDEX content_chunks_by_chunk ON content_chunks(chunk);
CREATE TABLE nodes(
    id INTEGER PRIMARY KEY,
    parent INTEGER REFERENCES nodes(id),
    name TEXT NOT NULL,
    content INTEGER REFERENCES contents(id),
    directory INTEGER NOT NULL,
    modified TEXT NOT NULL,
    UNIQUE(parent, name)
);
CREATE INDEX nodes_by_content ON nodes(content);
CREATE TABLE episodes(
    id TEXT PRIMARY KEY,
    node INTEGER UNIQUE REFERENCES nodes(id),
    origin TEXT NOT NULL,
    return_name TEXT,
    session TEXT,
    cleanup TEXT
) WITHOUT ROWID;
CREATE TABLE streams(
    episode TEXT NOT NULL,
    name TEXT NOT NULL,
    seq INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY(episode, name, seq)
) WITHOUT ROWID;
INSERT INTO nodes(id, parent, name, content, directory, modified) VALUES(1, NULL, '', NULL, 1, '');
";

#[derive(Clone, Debug)]
pub struct Node {
    pub id: i64,
    pub parent: Option<i64>,
    pub name: String,
    pub directory: bool,
    pub content: Option<i64>,
    pub size: u64,
    pub sha256: Option<String>,
    pub modified: String,
}

pub struct Library {
    pub(crate) conn: Connection,
    pub places: Places,
    pub store: Store,
}

const NODE: &str = "SELECT n.id, n.parent, n.name, n.directory, n.content, c.size, c.sha256, n.modified
                    FROM nodes n LEFT JOIN contents c ON c.id = n.content";

fn node(row: &rusqlite::Row) -> rusqlite::Result<Node> {
    Ok(Node {
        id: row.get(0)?,
        parent: row.get(1)?,
        name: row.get(2)?,
        directory: row.get(3)?,
        content: row.get(4)?,
        size: row.get::<_, Option<i64>>(5)?.unwrap_or(0) as u64,
        sha256: row.get(6)?,
        modified: row.get(7)?,
    })
}

/// The names in a library path. Paths are relative to the library and use `/`.
pub fn parts(path: &str) -> Result<Vec<&str>> {
    let names: Vec<&str> = path.split('/').filter(|name| !name.is_empty()).collect();
    if names.iter().any(|name| *name == "." || *name == ".." || name.contains('\0')) {
        return refuse(format!("A library path names each directory in full: {path}"));
    }
    Ok(names)
}

fn description(places: &Places) -> Result<Option<Value>> {
    let path = places.description();
    if !files::exists(&path) {
        return Ok(None);
    }
    let value = files::read(&path)?;
    if json::text(&value, "schema") != Some(SCHEMA) {
        return refuse(format!("{} does not describe a Berman library this build can open", path.display()));
    }
    Ok(Some(value))
}

impl Library {
    fn connect(places: &Places, key: &str) -> Result<Connection> {
        let conn = Connection::open(places.database())?;
        // A raw key skips key derivation, so a callback opens the library in milliseconds.
        conn.pragma_update(None, "key", format!("x'{key}'"))?;
        conn.busy_timeout(std::time::Duration::from_secs(60))?;
        conn.pragma_update(None, "temp_store", "MEMORY")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "secure_delete", "ON")?;
        conn.query_row("PRAGMA journal_mode = WAL", [], |_| Ok(()))?;
        conn.pragma_update(None, "synchronous", "NORMAL")?;
        conn.query_row("PRAGMA journal_size_limit = 67108864", [], |_| Ok(()))?;
        Ok(conn)
    }

    /// Create the library for this login. An existing library or key is never replaced.
    pub fn create(places: Places, store: Store) -> Result<Library> {
        let existing = [places.database(), places.description(), places.key_file()];
        if let Some(path) = existing.iter().find(|path| files::exists(path)) {
            return refuse(format!("A library already exists here and is unchanged: {}", path.display()));
        }
        places::private(&places.data)?;
        places::private(&places.data.join("locks"))?;
        let identity = files::uuid();
        let key = keys::create(store, &places, &identity)?;
        let made = (|| {
            // SQLite gives the write-ahead log the database file's permissions.
            let database = places.database();
            std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&database).at(&database)?;
            let conn = Library::connect(&places, &key)?;
            conn.execute_batch(&format!("BEGIN IMMEDIATE; {TABLES} COMMIT;"))?;
            conn.execute("INSERT INTO meta(key, value) VALUES('schema', ?1), ('id', ?2)", params![SCHEMA, identity])?;
            files::write(&places.description(), &json!({"schema": SCHEMA, "id": identity, "key": store.name()}))?;
            Ok(conn)
        })();
        match made {
            Ok(conn) => Ok(Library { conn, places, store }),
            Err(error) => {
                keys::discard(store, &places, &identity);
                for path in [places.database(), places.description()] {
                    let _ = std::fs::remove_file(path);
                }
                Err(error)
            }
        }
    }

    /// Open this login's library with its key.
    pub fn open() -> Result<Library> {
        Library::open_at(Places::find()?)
    }

    pub fn open_at(places: Places) -> Result<Library> {
        let Some(about) = description(&places)? else {
            if files::exists(&places.database()) {
                return refuse(format!(
                    "The library at {} has lost its description, library.json. The library is unchanged; \
                     restore that file from your backup.",
                    places.data.display()
                ));
            }
            return refuse("This login has no library yet. Create one with: berman library init");
        };
        if !files::exists(&places.database()) {
            return refuse(format!(
                "The library database is missing from {}. Restore library.db from your backup.",
                places.data.display()
            ));
        }
        let store = Store::named(json::text(&about, "key").unwrap_or_default())?;
        let key = keys::fetch(store, &places, json::text(&about, "id").unwrap_or_default())?;
        let conn = Library::connect(&places, &key)?;
        let schema: String = conn.query_row("SELECT value FROM meta WHERE key = 'schema'", [], |row| row.get(0))?;
        if schema != SCHEMA {
            return refuse(format!("This build cannot open a library with schema {schema}"));
        }
        Ok(Library { conn, places, store })
    }

    /// Run several changes as one. Either all of them are kept or none.
    pub fn transaction<T>(&self, change: impl FnOnce() -> Result<T>) -> Result<T> {
        if !self.conn.is_autocommit() {
            return change();
        }
        self.conn.execute_batch("BEGIN IMMEDIATE")?;
        match change() {
            Ok(value) => {
                self.conn.execute_batch("COMMIT")?;
                Ok(value)
            }
            Err(error) => {
                let _ = self.conn.execute_batch("ROLLBACK");
                Err(error)
            }
        }
    }

    /// Make the next commit reach the disk before it returns.
    pub fn durable(&self) -> Result<()> {
        self.conn.pragma_update(None, "synchronous", "FULL")?;
        Ok(self.conn.pragma_update(None, "fullfsync", "ON")?)
    }

    pub fn node(&self, id: i64) -> Result<Node> {
        Ok(self.conn.query_row(&format!("{NODE} WHERE n.id = ?1"), [id], node)?)
    }

    pub fn child(&self, parent: i64, name: &str) -> Result<Option<Node>> {
        let sql = format!("{NODE} WHERE n.parent = ?1 AND n.name = ?2");
        Ok(self.conn.prepare_cached(&sql)?.query_row(params![parent, name], node).optional()?)
    }

    /// The node a path names below a directory, if there is one.
    pub fn find(&self, base: i64, path: &str) -> Result<Option<Node>> {
        let mut current = self.node(base)?;
        for name in parts(path)? {
            match self.child(current.id, name)? {
                Some(next) if current.directory => current = next,
                _ => return Ok(None),
            }
        }
        Ok(Some(current))
    }

    pub fn expect(&self, base: i64, path: &str) -> Result<Node> {
        self.find(base, path)?
            .ok_or_else(|| Error::Missing(format!("No such file or directory in the library: {path}")))
    }

    /// The entries of a directory, in name order.
    pub fn children(&self, parent: i64) -> Result<Vec<Node>> {
        let sql = format!("{NODE} WHERE n.parent = ?1 ORDER BY n.name");
        let mut statement = self.conn.prepare_cached(&sql)?;
        let rows = statement.query_map([parent], node)?;
        Ok(rows.collect::<rusqlite::Result<Vec<Node>>>()?)
    }

    /// The library path of a node.
    pub fn path(&self, id: i64) -> Result<String> {
        let mut names = Vec::new();
        let mut current = self.node(id)?;
        while let Some(parent) = current.parent {
            names.push(current.name);
            current = self.node(parent)?;
        }
        names.reverse();
        Ok(names.join("/"))
    }

    fn insert(&self, parent: i64, name: &str, content: Option<i64>) -> Result<Node> {
        self.conn.execute(
            "INSERT INTO nodes(parent, name, content, directory, modified) VALUES(?1, ?2, ?3, ?4, ?5)",
            params![parent, name, content, content.is_none(), files::now()],
        )?;
        self.node(self.conn.last_insert_rowid())
    }

    /// The directory a path names, created with any missing parents.
    pub fn directories(&self, base: i64, names: &[&str]) -> Result<Node> {
        let mut current = self.node(base)?;
        for name in names {
            current = match self.child(current.id, name)? {
                Some(next) if next.directory => next,
                Some(_) => return refuse(format!("A file is in the way: {}", self.path(current.id)? + "/" + name)),
                None => self.insert(current.id, name, None)?,
            };
        }
        Ok(current)
    }

    /// Create a new directory. Its parents are created as needed; the directory itself must be new.
    pub fn make_directory(&self, base: i64, path: &str) -> Result<Node> {
        self.transaction(|| {
            let names = parts(path)?;
            let Some((last, parents)) = names.split_last() else {
                return refuse("Name the directory to create");
            };
            let parent = self.directories(base, parents)?;
            if self.child(parent.id, last)?.is_some() {
                return refuse(format!("Destination already exists in the library: {path}"));
            }
            self.insert(parent.id, last, None)
        })
    }

    /// Begin storing one file's bytes.
    pub fn writer(&self) -> Result<ContentWriter<'_>> {
        let pending = format!("pending-{}", files::uuid_hex());
        self.conn.execute("INSERT INTO contents(sha256, size) VALUES(?1, 0)", [&pending])?;
        Ok(ContentWriter {
            library: self,
            content: self.conn.last_insert_rowid(),
            buffer: Vec::with_capacity(CHUNK),
            sequence: 0,
            size: 0,
            hasher: Sha256::new(),
        })
    }

    /// Store bytes and return their content.
    pub fn content(&self, data: &[u8]) -> Result<Stored> {
        let mut writer = self.writer()?;
        writer.write_all(data).map_err(Error::from)?;
        writer.finish()
    }

    /// Drop a content nothing refers to any more, along with chunks only it used.
    fn release(&self, content: i64) -> Result<()> {
        let used: i64 =
            self.conn.query_row("SELECT count(*) FROM nodes WHERE content = ?1", [content], |r| r.get(0))?;
        if used > 0 {
            return Ok(());
        }
        self.conn.execute_batch("CREATE TEMP TABLE IF NOT EXISTS released(chunk INTEGER PRIMARY KEY)")?;
        self.conn.execute("DELETE FROM released", [])?;
        self.conn
            .execute("INSERT OR IGNORE INTO released SELECT chunk FROM content_chunks WHERE content = ?1", [content])?;
        self.conn.execute("DELETE FROM content_chunks WHERE content = ?1", [content])?;
        self.conn.execute("DELETE FROM contents WHERE id = ?1", [content])?;
        self.conn.execute(
            "DELETE FROM chunks WHERE id IN (SELECT chunk FROM released)
             AND NOT EXISTS (SELECT 1 FROM content_chunks WHERE content_chunks.chunk = chunks.id)",
            [],
        )?;
        Ok(())
    }

    /// Give a path a file with stored content, creating parent directories.
    pub fn place(&self, base: i64, path: &str, stored: &Stored, replace: bool) -> Result<Node> {
        let names = parts(path)?;
        let Some((last, parents)) = names.split_last() else {
            return refuse("Name the file to write");
        };
        let parent = self.directories(base, parents)?;
        match self.child(parent.id, last)? {
            None => self.insert(parent.id, last, Some(stored.content)),
            Some(existing) if existing.directory => refuse(format!("A directory is in the way: {path}")),
            Some(_) if !replace => refuse(format!("Destination already exists in the library: {path}")),
            Some(existing) => {
                self.conn.execute(
                    "UPDATE nodes SET content = ?1, modified = ?2 WHERE id = ?3",
                    params![stored.content, files::now(), existing.id],
                )?;
                if let Some(old) = existing.content.filter(|old| *old != stored.content) {
                    self.release(old)?;
                }
                self.node(existing.id)
            }
        }
    }

    /// Write a small file, replacing an earlier version.
    pub fn write(&self, base: i64, path: &str, data: &[u8]) -> Result<Node> {
        self.transaction(|| self.place(base, path, &self.content(data)?, true))
    }

    pub fn write_json(&self, base: i64, path: &str, value: &Value) -> Result<Node> {
        self.write(base, path, &json::bytes(value))
    }

    /// Stream a content's bytes to a writer.
    pub fn stream(&self, content: i64, out: &mut dyn Write) -> Result<()> {
        let mut statement = self.conn.prepare_cached(
            "SELECT k.data FROM content_chunks cc JOIN chunks k ON k.id = cc.chunk WHERE cc.content = ?1 ORDER BY cc.seq",
        )?;
        let mut rows = statement.query([content])?;
        while let Some(row) = rows.next()? {
            out.write_all(row.get_ref(0)?.as_blob().map_err(rusqlite::Error::from)?)?;
        }
        Ok(())
    }

    /// The SHA-256 and length of the bytes actually stored for a content.
    pub fn measure(&self, content: i64) -> Result<(String, u64)> {
        struct Measure(Sha256, u64);
        impl Write for Measure {
            fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
                self.0.update(data);
                self.1 += data.len() as u64;
                Ok(data.len())
            }
            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }
        let mut measure = Measure(Sha256::new(), 0);
        self.stream(content, &mut measure)?;
        Ok((files::hex(&measure.0.finalize()), measure.1))
    }

    /// Read a whole file. Meant for the small JSON and text members of an episode.
    pub fn read(&self, base: i64, path: &str) -> Result<Vec<u8>> {
        let found = self.expect(base, path)?;
        let Some(content) = found.content else {
            return refuse(format!("Expected a file in the library: {path}"));
        };
        let mut data = Vec::with_capacity(found.size as usize);
        self.stream(content, &mut data)?;
        Ok(data)
    }

    pub fn read_json(&self, base: i64, path: &str) -> Result<Value> {
        Ok(serde_json::from_slice(&self.read(base, path)?)?)
    }

    pub fn exists(&self, base: i64, path: &str) -> Result<bool> {
        Ok(self.find(base, path)?.is_some())
    }

    fn below(&self, top: i64, id: i64) -> Result<bool> {
        let mut current = Some(id);
        while let Some(at) = current {
            if at == top {
                return Ok(true);
            }
            current = self.node(at)?.parent;
        }
        Ok(false)
    }

    fn duplicate(&self, source: &Node, parent: i64, name: &str) -> Result<Node> {
        let made = self.insert(parent, name, source.content)?;
        if source.directory {
            for entry in self.children(source.id)? {
                self.duplicate(&entry, made.id, &entry.name)?;
            }
        }
        Ok(made)
    }

    /// Where a new entry for a destination path goes. An existing directory receives the source's own name.
    fn destination(&self, source: &Node, path: &str) -> Result<(i64, String)> {
        let names = parts(path)?;
        if let Some(existing) = self.find(ROOT, path)? {
            if !existing.directory {
                return refuse(format!("Destination already exists in the library: {path}"));
            }
            if self.child(existing.id, &source.name)?.is_some() || source.parent.is_none() {
                return refuse(format!("Destination already exists in the library: {path}/{}", source.name));
            }
            return Ok((existing.id, source.name.clone()));
        }
        let Some((last, parents)) = names.split_last() else {
            return refuse("Name the destination");
        };
        Ok((self.directories(ROOT, parents)?.id, (*last).to_string()))
    }

    /// Copy a file or directory within the library. The copy shares stored bytes with its source.
    pub fn copy(&self, source: &str, destination: &str) -> Result<Node> {
        self.transaction(|| {
            let from = self.expect(ROOT, source)?;
            let (parent, name) = self.destination(&from, destination)?;
            if from.directory && self.below(from.id, parent)? {
                return refuse("A directory cannot be copied into itself");
            }
            self.duplicate(&from, parent, &name)
        })
    }

    /// Rename or move a file or directory within the library.
    pub fn rename(&self, source: &str, destination: &str) -> Result<Node> {
        self.transaction(|| {
            let from = self.expect(ROOT, source)?;
            if from.parent.is_none() {
                return refuse("The library itself cannot be renamed");
            }
            let (parent, name) = self.destination(&from, destination)?;
            if self.below(from.id, parent)? {
                return refuse("A directory cannot be moved into itself");
            }
            self.conn.execute(
                "UPDATE nodes SET parent = ?1, name = ?2, modified = ?3 WHERE id = ?4",
                params![parent, name, files::now(), from.id],
            )?;
            self.node(from.id)
        })
    }

    /// Every node at or below one, parents before children.
    fn subtree(&self, top: i64) -> Result<Vec<Node>> {
        let mut found = vec![self.node(top)?];
        let mut next = 0;
        while next < found.len() {
            let id = found[next].id;
            if found[next].directory {
                found.extend(self.children(id)?);
            }
            next += 1;
        }
        Ok(found)
    }

    /// Remove a file, or a directory with everything below it.
    pub fn remove(&self, target: &Node) -> Result<()> {
        self.transaction(|| {
            if target.parent.is_none() {
                return refuse("The library itself cannot be removed");
            }
            let nodes = self.subtree(target.id)?;
            let open: Option<String> = self
                .conn
                .query_row(
                    "SELECT id FROM episodes WHERE return_name IS NOT NULL AND node IN (SELECT value FROM json_each(?1))",
                    [json::line(&json!(nodes.iter().map(|n| n.id).collect::<Vec<i64>>()))],
                    |row| row.get(0),
                )
                .optional()?;
            if let Some(episode) = open {
                return refuse(format!("Episode {episode} still has a return directory. Close it before removing it."));
            }
            for found in nodes.iter().rev() {
                self.conn.execute("UPDATE episodes SET node = NULL WHERE node = ?1", [found.id])?;
                self.conn.execute("DELETE FROM nodes WHERE id = ?1", [found.id])?;
            }
            for content in nodes.iter().filter_map(|found| found.content) {
                self.release(content)?;
            }
            Ok(())
        })
    }

    /// Every file at or below a directory with its path relative to it: a directory's files in name order, then
    /// each of its directories in name order.
    pub fn files(&self, top: i64) -> Result<Vec<(String, Node)>> {
        let mut found = Vec::new();
        let mut pending = vec![(String::new(), top)];
        while let Some((prefix, directory)) = pending.pop() {
            let mut below = Vec::new();
            for entry in self.children(directory)? {
                let name = format!("{prefix}{}", entry.name);
                if entry.directory {
                    below.push((format!("{name}/"), entry.id));
                } else {
                    found.push((name, entry));
                }
            }
            pending.extend(below.into_iter().rev());
        }
        Ok(found)
    }

    /// Copy a file or directory from the filesystem into the library, reading regular files only.
    pub fn put(&self, local: &Path, destination: &str, replace: bool) -> Result<Node> {
        let metadata = local.symlink_metadata().at(local)?;
        self.transaction(|| {
            if metadata.is_file() {
                let stored = self.store_file(local, None)?;
                return self.place(ROOT, destination, &stored, replace);
            }
            if !metadata.is_dir() {
                return refuse(format!("Expected a regular file or a directory: {}", local.display()));
            }
            let top = self.make_directory(ROOT, destination)?;
            let mut pending = vec![(local.to_path_buf(), top.id)];
            while let Some((directory, node)) = pending.pop() {
                let mut entries: Vec<_> = std::fs::read_dir(&directory).at(&directory)?.flatten().collect();
                entries.sort_by_key(|entry| entry.file_name());
                for entry in entries {
                    let path = entry.path();
                    let Some(name) = entry.file_name().to_str().map(str::to_string) else {
                        return refuse(format!("The library keeps names as UTF-8 text: {}", path.display()));
                    };
                    let kind = path.symlink_metadata().at(&path)?;
                    if kind.is_dir() {
                        pending.push((path, self.insert(node, &name, None)?.id));
                    } else if kind.is_file() {
                        let stored = self.store_file(&path, None)?;
                        self.insert(node, &name, Some(stored.content))?;
                    } else {
                        return refuse(format!("Cannot copy a symbolic link or special file: {}", path.display()));
                    }
                }
            }
            self.node(top.id)
        })
    }

    /// Store a regular file's bytes, refusing one that changes during the read or exceeds a limit.
    pub fn store_file(&self, path: &Path, limit: Option<u64>) -> Result<Stored> {
        files::regular(path)?;
        let before = std::fs::metadata(path).at(path)?;
        if limit.is_some_and(|limit| before.len() > limit) {
            return refuse(format!("File exceeds {} bytes: {}", limit.unwrap_or_default(), path.display()));
        }
        let mut writer = self.writer()?;
        let mut source = std::fs::File::open(path).at(path)?.take(before.len() + 1);
        std::io::copy(&mut source, &mut writer).at(path)?;
        let stored = writer.finish()?;
        let after = std::fs::metadata(path).at(path)?;
        if files::identity(&before) != files::identity(&after) || stored.size != after.len() {
            return refuse(format!("Source changed during observation: {}", path.display()));
        }
        Ok(stored)
    }

    /// Write a real file or directory at a fresh destination outside the library.
    ///
    /// Names are created before any bytes are written, so a filesystem that cannot hold two of
    /// the library's distinct names side by side is reported and nothing is left behind.
    pub fn export(&self, source: &Node, destination: &Path) -> Result<()> {
        let create = |path: &Path| std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path);
        if !source.directory {
            if files::exists(destination) {
                return refuse(format!("Destination already exists: {}", destination.display()));
            }
            let parent = destination.parent().unwrap_or(Path::new("/"));
            std::fs::create_dir_all(parent).at(parent)?;
            let mut out = create(destination).at(destination)?;
            let written = self.stream(source.content.unwrap_or_default(), &mut out).and_then(|()| Ok(out.sync_all()?));
            if written.is_err() {
                let _ = std::fs::remove_file(destination);
            }
            return written;
        }
        files::new_directory(destination, |stage| {
            let members = self.files(source.id)?;
            let mut collisions = Vec::new();
            let mut directories = vec![(String::new(), source.id)];
            while let Some((prefix, id)) = directories.pop() {
                for entry in self.children(id)?.into_iter().filter(|entry| entry.directory) {
                    let name = format!("{prefix}{}", entry.name);
                    if std::fs::create_dir(stage.join(&name)).is_err() {
                        collisions.push(name.clone());
                    }
                    directories.push((format!("{name}/"), entry.id));
                }
            }
            for (name, _) in &members {
                if create(&stage.join(name)).is_err() {
                    collisions.push(name.clone());
                }
            }
            if !collisions.is_empty() {
                return refuse(format!(
                    "This filesystem cannot hold these names beside others in the same directory: {}. \
                     Nothing was exported.",
                    collisions.join(", ")
                ));
            }
            for (name, member) in &members {
                let path = stage.join(name);
                let mut out = std::fs::OpenOptions::new().write(true).open(&path).at(&path)?;
                self.stream(member.content.unwrap_or_default(), &mut out)?;
                out.sync_all().at(&path)?;
            }
            Ok(())
        })
    }
}

/// Bytes kept in the library, identified by their digest.
#[derive(Clone, Debug)]
pub struct Stored {
    pub content: i64,
    pub size: u64,
    pub sha256: String,
}

/// Receives one file's bytes and stores them as chunks of bounded size.
pub struct ContentWriter<'a> {
    library: &'a Library,
    content: i64,
    buffer: Vec<u8>,
    sequence: i64,
    size: u64,
    hasher: Sha256,
}

impl ContentWriter<'_> {
    fn chunk(&mut self) -> Result<()> {
        let digest = Sha256::digest(&self.buffer);
        let conn = &self.library.conn;
        conn.prepare_cached("INSERT INTO chunks(sha256, data) VALUES(?1, ?2) ON CONFLICT(sha256) DO NOTHING")?
            .execute(params![digest.as_slice(), self.buffer])?;
        conn.prepare_cached(
            "INSERT INTO content_chunks(content, seq, chunk) SELECT ?1, ?2, id FROM chunks WHERE sha256 = ?3",
        )?
        .execute(params![self.content, self.sequence, digest.as_slice()])?;
        self.sequence += 1;
        self.buffer.clear();
        Ok(())
    }

    pub fn size(&self) -> u64 {
        self.size
    }

    /// The digest of the bytes received so far.
    pub fn digest_so_far(&self) -> String {
        files::hex(&self.hasher.clone().finalize())
    }

    /// Finish the file. Bytes the library already holds are shared, not stored again.
    pub fn finish(mut self) -> Result<Stored> {
        if !self.buffer.is_empty() {
            self.chunk()?;
        }
        let sha256 = files::hex(&self.hasher.clone().finalize());
        let conn = &self.library.conn;
        let known: Option<i64> =
            conn.query_row("SELECT id FROM contents WHERE sha256 = ?1", [&sha256], |row| row.get(0)).optional()?;
        if let Some(existing) = known {
            conn.execute("DELETE FROM content_chunks WHERE content = ?1", [self.content])?;
            conn.execute("DELETE FROM contents WHERE id = ?1", [self.content])?;
            // Chunks the abandoned copy alone introduced are equal to the existing content's, by digest.
            return Ok(Stored { content: existing, size: self.size, sha256 });
        }
        conn.execute(
            "UPDATE contents SET sha256 = ?1, size = ?2 WHERE id = ?3",
            params![sha256, self.size as i64, self.content],
        )?;
        Ok(Stored { content: self.content, size: self.size, sha256 })
    }
}

impl Write for ContentWriter<'_> {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        let mut rest = data;
        while !rest.is_empty() {
            let take = rest.len().min(CHUNK - self.buffer.len());
            self.buffer.extend_from_slice(&rest[..take]);
            rest = &rest[take..];
            if self.buffer.len() == CHUNK {
                self.chunk().map_err(|error| std::io::Error::other(error.to_string()))?;
            }
        }
        self.hasher.update(data);
        self.size += data.len() as u64;
        Ok(data.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// Read until a buffer is full or the stream ends.
pub fn fill(stream: &mut dyn Read, buffer: &mut [u8]) -> std::io::Result<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match stream.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(count) => filled += count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(filled)
}

/// What the library knows about an episode beyond the files in its directory.
#[derive(Clone, Debug)]
pub struct Episode {
    pub id: String,
    /// The episode's directory, wherever the producer has moved it. None once it has been removed.
    pub node: Option<i64>,
    /// `opened` here, or `imported` from a record made elsewhere.
    pub origin: String,
    /// The opaque name of the return directory while one exists.
    pub return_name: Option<String>,
    /// The native session bound to the episode.
    pub session: Option<String>,
    /// The collected return files still to be retired, as JSON.
    pub cleanup: Option<String>,
}

fn episode(row: &rusqlite::Row) -> rusqlite::Result<Episode> {
    Ok(Episode {
        id: row.get(0)?,
        node: row.get(1)?,
        origin: row.get(2)?,
        return_name: row.get(3)?,
        session: row.get(4)?,
        cleanup: row.get(5)?,
    })
}

const EPISODE: &str = "SELECT id, node, origin, return_name, session, cleanup FROM episodes";

impl Library {
    pub fn episode(&self, id: &str) -> Result<Option<Episode>> {
        Ok(self.conn.query_row(&format!("{EPISODE} WHERE id = ?1"), [id], episode).optional()?)
    }

    pub fn episode_at(&self, node: i64) -> Result<Option<Episode>> {
        Ok(self.conn.query_row(&format!("{EPISODE} WHERE node = ?1"), [node], episode).optional()?)
    }

    pub fn episodes(&self) -> Result<Vec<Episode>> {
        let mut statement = self.conn.prepare(&format!("{EPISODE} ORDER BY id"))?;
        let rows = statement.query_map([], episode)?;
        Ok(rows.collect::<rusqlite::Result<Vec<Episode>>>()?)
    }

    pub fn add_episode(&self, id: &str, node: i64, origin: &str, return_name: Option<&str>) -> Result<()> {
        self.conn.execute(
            "INSERT INTO episodes(id, node, origin, return_name) VALUES(?1, ?2, ?3, ?4)",
            params![id, node, origin, return_name],
        )?;
        Ok(())
    }

    pub fn bind_session(&self, id: &str, session: &str) -> Result<()> {
        self.conn
            .execute("UPDATE episodes SET session = ?2 WHERE id = ?1 AND session IS NULL", params![id, session])?;
        Ok(())
    }

    /// Record which collected return files remain to be retired, or that none do.
    pub fn set_cleanup(&self, id: &str, inventory: Option<&Value>) -> Result<()> {
        let text = inventory.map(json::line);
        self.conn.execute("UPDATE episodes SET cleanup = ?2 WHERE id = ?1", params![id, text])?;
        Ok(())
    }

    pub fn forget_return(&self, id: &str) -> Result<()> {
        self.conn.execute("UPDATE episodes SET return_name = NULL, cleanup = NULL WHERE id = ?1", [id])?;
        Ok(())
    }

    /// Keep a piece of a sitting's native output as it arrives.
    pub fn append_stream(&self, episode: &str, name: &str, data: &[u8]) -> Result<()> {
        self.conn
            .prepare_cached(
                "INSERT INTO streams(episode, name, seq, data)
                 SELECT ?1, ?2, coalesce(max(seq), 0) + 1, ?3 FROM streams WHERE episode = ?1 AND name = ?2",
            )?
            .execute(params![episode, name, data])?;
        Ok(())
    }

    /// Mark that a stream's writers have closed it, and whether bytes beyond the limit were dropped.
    pub fn end_stream(&self, episode: &str, name: &str, cut: bool) -> Result<()> {
        let mark: &[u8] = if cut { b"cut" } else { b"" };
        self.conn.execute(
            "INSERT OR REPLACE INTO streams(episode, name, seq, data) VALUES(?1, ?2, 0, ?3)",
            params![episode, name, mark],
        )?;
        Ok(())
    }

    /// Turn the pieces kept for a stream into a file of the episode. Returns whether the stream was cut at the limit.
    ///
    /// A relay that is still carrying output is given a short time to finish.
    pub fn settle_stream(&self, episode: &str, node: i64, name: &str) -> Result<bool> {
        let ended = || -> Result<Option<Vec<u8>>> {
            let sql = "SELECT data FROM streams WHERE episode = ?1 AND name = ?2 AND seq = 0";
            Ok(self.conn.query_row(sql, params![episode, name], |row| row.get(0)).optional()?)
        };
        let patience = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while ended()?.is_none() && !self.exists(node, name)? && std::time::Instant::now() < patience {
            std::thread::sleep(std::time::Duration::from_millis(25));
        }
        self.transaction(|| {
            let cut = ended()?.is_some_and(|mark| !mark.is_empty());
            if self.exists(node, name)? {
                return Ok(cut);
            }
            let mut writer = self.writer()?;
            {
                let sql = "SELECT data FROM streams WHERE episode = ?1 AND name = ?2 AND seq > 0 ORDER BY seq";
                let mut statement = self.conn.prepare(sql)?;
                let mut rows = statement.query(params![episode, name])?;
                while let Some(row) = rows.next()? {
                    writer.write_all(row.get_ref(0)?.as_blob().map_err(rusqlite::Error::from)?)?;
                }
            }
            let stored = writer.finish()?;
            self.place(node, name, &stored, false)?;
            self.conn.execute("DELETE FROM streams WHERE episode = ?1 AND name = ?2", params![episode, name])?;
            Ok(cut)
        })
    }

    pub fn setting(&self, key: &str) -> Result<Option<String>> {
        Ok(self.conn.query_row("SELECT value FROM meta WHERE key = ?1", [key], |row| row.get(0)).optional()?)
    }

    pub fn set_setting(&self, key: &str, value: &str) -> Result<()> {
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?1, ?2)", params![key, value])?;
        Ok(())
    }
}
