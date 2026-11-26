//! Identify a running process by when it started, so a recycled process ID is not mistaken for an actor.

/// When a live process started, in the operating system's own terms, or None once it has ended.
#[cfg(target_os = "linux")]
pub fn started(pid: i64) -> Option<String> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let fields: Vec<&str> = stat[stat.rfind(')')? + 1..].split_whitespace().collect();
    if fields.first() == Some(&"Z") {
        return None;
    }
    fields.get(19).map(|field| (*field).to_string())
}

#[cfg(target_os = "macos")]
pub fn started(pid: i64) -> Option<String> {
    const ZOMBIE: u32 = 5;
    let pid = libc::c_int::try_from(pid).ok()?;
    let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
    // SAFETY: the buffer is a zeroed proc_bsdinfo of the size passed, which the call fills.
    let info = unsafe {
        let mut info: libc::proc_bsdinfo = std::mem::zeroed();
        if libc::proc_pidinfo(pid, libc::PROC_PIDTBSDINFO, 0, (&raw mut info).cast(), size) != size {
            return None;
        }
        info
    };
    if info.pbi_status == ZOMBIE {
        return None;
    }
    Some(format!("{}.{:06}", info.pbi_start_tvsec, info.pbi_start_tvusec))
}
