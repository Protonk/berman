//! JSON as the records have always spelled it: two-space indentation for files, and sorted keys
//! with spaced separators for digests and history lines.

use std::io;

use serde::Serialize;
use serde_json::ser::{Formatter, PrettyFormatter, Serializer};
use serde_json::{Map, Value};

use crate::files;

struct Spaced;

impl Formatter for Spaced {
    fn begin_array_value<W: ?Sized + io::Write>(&mut self, writer: &mut W, first: bool) -> io::Result<()> {
        if first { Ok(()) } else { writer.write_all(b", ") }
    }

    fn begin_object_key<W: ?Sized + io::Write>(&mut self, writer: &mut W, first: bool) -> io::Result<()> {
        if first { Ok(()) } else { writer.write_all(b", ") }
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, writer: &mut W) -> io::Result<()> {
        writer.write_all(b": ")
    }
}

/// Indented by two spaces, with non-ASCII text left as it is.
pub fn pretty(value: &Value) -> String {
    let mut out = Vec::new();
    let mut serializer = Serializer::with_formatter(&mut out, PrettyFormatter::with_indent(b"  "));
    value.serialize(&mut serializer).expect("a JSON value serializes");
    String::from_utf8(out).expect("serialized JSON is UTF-8")
}

/// The bytes of a JSON file: indented and ending in a newline.
pub fn bytes(value: &Value) -> Vec<u8> {
    let mut text = pretty(value);
    text.push('\n');
    text.into_bytes()
}

/// One line, with a space after each comma and colon.
pub fn line(value: &Value) -> String {
    let mut out = Vec::new();
    let mut serializer = Serializer::with_formatter(&mut out, Spaced);
    value.serialize(&mut serializer).expect("a JSON value serializes");
    String::from_utf8(out).expect("serialized JSON is UTF-8")
}

fn sorted(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = Map::new();
            for key in keys {
                out.insert(key.clone(), sorted(&map[key]));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted).collect()),
        other => other.clone(),
    }
}

/// Digest a JSON value using sorted keys and spaced separators.
pub fn digest(value: &Value) -> String {
    files::digest(line(&sorted(value)).as_bytes())
}

/// Whether a value counts as present: null, false, zero and empty text or containers do not.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|n| n != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// A value as it appears inside a line of a reading copy.
pub fn display(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(text) => text.clone(),
        other => line(other),
    }
}

/// A string member of an object, if it is one.
pub fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}
