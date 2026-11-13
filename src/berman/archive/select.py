"""Select an edition's runs from the verified recorded inventory (stdlib only).

Runs come from source-layout.json, never from recency, and each run's connection from Berman's
recorded attributions, never from a project name. Every recorded run is inventoried whichever runs
an edition then selects. A Build is the whole input to rendering and packaging; nothing here keeps
state between builds.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat

from berman.archive import stewart

# Portable edition namespace, independent of the local repository name (berman).
PREFIX = "stewart-analysis/"
BERMAN_PREFIX = "berman-analysis/"
ARCHIVE = "archive/stewart-runs.zip"
CONTEXT_DOCUMENTS = ("README.md", "LIMITS.md", "framing/working_in_a_berman_archive.md",
                     "framing/better_software_through_grounded_theory.md",
                     "framing/the_provenance_of_encounters.md", "framing/five_suspicions.md", "inquiry/README.md",
                     "SESSION-CAPTURE.txt", "riker/RIKER.md", "riker/THREAD-ONRAMP-TEMPLATE.md",
                     "handoffs/ANALYSIS-RUNS-001-005.md",
                     "inquiry/2026-10-22-riker-design-judgment.md",
                     "handoffs/FIELDWORKER-SUPPORT-AUDIT-REPORT.md",
                     "handoffs/FIELDWORKER-SUPPORT-REMEDIATION.md",
                     "experiments/fieldworker-support-2026-10-22/README.md")
# A cross-project edition accompanies its runs with Berman's framing, not Stewart's edition framing.
EDITION_DOCUMENTS = ("README.md", "BERMAN.md", "LIMITS.md", "framing/working_in_a_berman_archive.md",
                     "framing/better_software_through_grounded_theory.md",
                     "framing/the_provenance_of_encounters.md", "framing/five_suspicions.md", "inquiry/README.md",
                     "SESSION-CAPTURE.txt")
SHA = lambda data: hashlib.sha256(data).hexdigest()
# A run without an attribution is read as historical Stewart material.
LEGACY = {"id": "stewart", "name": "Stewart", "readers": [stewart.READER],
          "basis": "legacy single-project convention", "harness": "codex", "checkouts": []}


@dataclass(frozen=True)
class Edition:
    """An edition's identity: reader label, ZIP namespace and the connections it selects."""
    name: str
    label: str
    prefix: str
    connections: tuple


# The default edition reproduces the cumulative Stewart edition.
STEWART = Edition("stewart", "STEWART FIELDWORK", PREFIX, ("stewart",))


@dataclass(frozen=True)
class Build:
    """One edition's inputs; rendering and packaging read only this and the files it names."""
    root: Path
    edition: Edition
    layout: dict        # source layout of the selected runs
    entries: list       # verified inventory rows beneath the selected run roots
    runs: tuple
    attributions: dict  # run -> connection attribution, for every recorded run
    subset: bool        # some recorded runs are left out; the package carries the restricted layout
    separate: bool      # runs were named: a separate ZIP, with no reading aids in the tree
    archive_path: str

    @property
    def tree(self):
        """Only the cumulative Stewart edition writes reading aids into the working tree."""
        return self.edition.name == "stewart" and not self.separate

    @property
    def files(self):
        return {r["path"]: r for r in self.entries if r["type"] == "file"}

    @property
    def origins(self):
        """Run -> recorded original root; historical argv used those locations."""
        return {r["run"]: r["original_root"] for r in self.layout["runs"] if r.get("original_root")}

    @property
    def documents(self):
        """Current documents packaged with the edition; Stewart's framing accompanies Stewart runs only."""
        if self.edition.name == "stewart":
            return CONTEXT_DOCUMENTS
        path = self.root / 'edition.json'
        value = load(self.root, 'edition.json') if path.is_file() else {'schema': 'berman.edition/1', 'documents': []}
        if value.get('schema') != 'berman.edition/1':
            raise ValueError('Unsupported edition.json schema')
        names = value.get('documents', [])
        if not isinstance(names, list) or any(not isinstance(n, str) or not n or Path(n).is_absolute()
                                             or '..' in Path(n).parts for n in names):
            raise ValueError('Edition documents must be relative paths within the workspace')
        return tuple(dict.fromkeys(names))

    def connection(self, run):
        return self.attributions.get(run, LEGACY)

    def stewart_reader(self, run):
        return stewart.READER in self.connection(run)["readers"]


def load(root, path):
    return json.loads((root / path).read_text(encoding="utf-8"))


def inventory(root):
    """Check every recorded run against source-layout.json; return the layout and observed rows."""
    layout = load(root, "source-layout.json")
    rows = []
    for run in layout["runs"]:
        base = root / run["current_root"]
        if 'root_mtime_ns' in run and (base.stat().st_mtime_ns != run['root_mtime_ns'] or oct(stat.S_IMODE(base.stat().st_mode)) != run['root_mode']):
            raise ValueError(f"Original root metadata changed since relocation: {run['run']}")
        prior_entries = {entry['path']: entry for entry in run['entries']}
        observed = []
        paths = [base]
        for parent, dirs, files in os.walk(base, followlinks=False):
            paths.extend(Path(parent) / name for name in dirs + files)
        for path in sorted(paths):
            st = path.lstat()
            item = {"path": path.relative_to(root).as_posix(), "mode": oct(stat.S_IMODE(st.st_mode)), "mtime_ns": st.st_mtime_ns}
            if stat.S_ISLNK(st.st_mode):
                data = os.fsencode(os.readlink(path))
                item.update(type="symlink", target=os.readlink(path), size=len(data), sha256=SHA(data))
            elif stat.S_ISDIR(st.st_mode):
                item.update(type="directory", size="", sha256="", target="")
            elif stat.S_ISREG(st.st_mode):
                data = path.read_bytes()
                item.update(type="file", size=len(data), sha256=SHA(data), target="")
            else:
                raise ValueError(f"Unsupported original entry: {path}")
            rows.append(item)
            if path != base:
                old = {"path": path.relative_to(base).as_posix(), "mode": item["mode"], "type": item["type"]}
                if 'mtime_ns' in prior_entries.get(old['path'], {}):
                    old['mtime_ns'] = item['mtime_ns']
                if item["type"] == "file":
                    old.update(size=item["size"], sha256=item["sha256"])
                elif item["type"] == "symlink":
                    old.update(target=item["target"])
                observed.append(old)
        if sorted(observed, key=lambda x: x["path"]) != run["entries"]:
            raise ValueError(f"Original evidence changed since relocation: {run['run']}")
        pointers = run.get("git_pointers", [run["git_pointer"]] if "git_pointer" in run else [])
        for record in pointers:
            pointer = base / record["path"]
            if pointer.read_text() != record["content"]:
                raise ValueError(f"Git pointer changed: {pointer}")
    names = [run["run"] for run in layout["runs"]]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate run identity in source-layout.json")
    return layout, rows


def attribute(root, layout):
    """Associate runs with connections through Berman's recorded attributions, never a project name."""
    config = load(root, "berman.json") if (root / "berman.json").is_file() else {}
    definitions, checkouts = {}, {}
    if config.get("schema") == "berman.workspace/1":
        for ident, entry in config["connections"].items():
            definition = load(root, entry["definition"])
            definitions[ident] = {"id": ident, "name": definition.get("name", ident), "readers": definition.get("readers", [])}
            checkout = Path(entry["checkout"]).expanduser()
            checkouts.setdefault(ident, set()).add(os.path.abspath(checkout if checkout.is_absolute() else root / checkout))
        legacy = config.get("legacy_connection")
        catalogue = load(root, config["catalogue"]) if config.get("catalogue") else {"runs": []}
    else:
        definitions["stewart"] = {"id": "stewart", "name": "Stewart", "readers": [stewart.READER]}
        legacy, catalogue = "stewart", {"runs": []}
    catalogued = {row["run"]: row["connection"] for row in catalogue["runs"]}
    result = {}
    for run in (r["run"] for r in layout["runs"]):
        path = root / "operations" / run / "operation.json"
        record = json.loads(path.read_text()) if path.is_file() else {}
        ident, basis = (record.get("connection") or {}).get("id"), "operation record"
        if not ident and run in catalogued:
            ident, basis = catalogued[run], "run catalogue"
        if not ident:
            ident, basis = legacy, "legacy single-project convention"
        if record.get("project"):
            checkouts.setdefault(ident, set()).add(record["project"])
        result[run] = dict(definitions.get(ident, {"id": ident, "name": ident or "unattributed", "readers": []}),
                           basis=basis, harness=(record.get("harness") or {}).get("id", "codex"))
    for row in result.values():
        row["checkouts"] = sorted(checkouts.get(row["id"], ()))
    return result


def select(root, edition="stewart", connections=(), runs=(), recorded=None):
    """Choose an edition's runs explicitly; inventory checks still cover every recorded run.

    recorded, an earlier inventory(root), lets several editions share one inventory.
    """
    root = Path(root)
    layout, entries = inventory(root) if recorded is None else recorded
    # Attribution covers the full recorded layout, so unselected runs' checkouts still classify references.
    attributions = attribute(root, layout)
    connection = lambda run: attributions.get(run, LEGACY)
    if edition == "stewart":
        if connections:
            raise ValueError("The Stewart edition selects Stewart's runs; use --edition connections to choose connections")
        chosen = STEWART
        selected = [r for r in layout["runs"] if stewart.READER in connection(r["run"])["readers"]]
    else:
        if not connections:
            raise ValueError("--edition connections needs at least one --connection")
        known = {c["id"] for c in attributions.values()}
        if set(connections) - known:
            raise ValueError("No recorded runs for connection(s): " + ", ".join(sorted(set(connections) - known)))
        chosen = Edition("connections", "BERMAN FIELDWORK", BERMAN_PREFIX, tuple(sorted(set(connections))))
        selected = [r for r in layout["runs"] if connection(r["run"])["id"] in connections]
    if runs:
        outside = set(runs) - {r["run"] for r in selected}
        if outside:
            raise ValueError("Run(s) outside this edition's connections: " + ", ".join(sorted(outside)))
        selected = [r for r in selected if r["run"] in runs]
    if not selected:
        raise ValueError("No recorded runs are selected for this edition")
    names = tuple(r["run"] for r in selected)
    subset = len(selected) != len(layout["runs"])
    if subset:
        roots = {r["current_root"] for r in selected}
        layout = dict(layout, runs=selected)
        entries = [e for e in entries if any(e["path"] == base or e["path"].startswith(base + "/") for base in roots)]
    suffix = (names[0] if len(names) == 1 else "runs-" + "-".join(run.removeprefix("run-") for run in names)) if runs else ""
    if chosen.name == "stewart":
        archive_path = "archive/stewart-" + suffix + ".zip" if runs else ARCHIVE
    else:
        archive_path = "archive/editions/berman-" + "-".join(chosen.connections) + ("-" + suffix if suffix else "") + ".zip"
    return Build(root, chosen, layout, entries, names, attributions, subset, bool(runs), archive_path)
