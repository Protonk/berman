"""Stewart's specialized archive readers and historical annotations.

The archive builder selects these explicitly for runs whose connection lists the
stewart-historical reader. They describe Stewart's evidence-export packages, its auditor
streams, historical recording programs and the coverage of runs 001-008. Other connections'
runs are read only by the common machinery.
"""
from pathlib import Path

READER = 'stewart-historical'
EVIDENCE_SCHEMA = 'stewart.evidence-package/'
CONVERSATION = 'manager conversation'

DESCRIPTIONS = {
    "record-command.py": ("fieldwork recording", "Runs argv; saves time, cwd, exit, stdout/stderr; appends direct Git invocations to the fieldwork log."),
    "stewart-call.py": ("manager interaction wrapper", "Adds interpreter/repository/workspace arguments, delegates recording, and prints a selected JSON view. The saved stdout retains the original output."),
    "watch-auditor.py": ("external observation", "Spawns the saved launch shell, saves its streams and records watcher events outside Stewart."),
    "manager-inventory.py": ("manager study probe", "Static AST inventory of source expressions; preserved as study material."),
    "experiment.py": ("auditor study probe", "Comparison experiment authored for the instrument-discrimination assignment."),
    "projection-spec.json": ("view transformation specification", "Lists copied and omitted status fields and selection conditions; original source snapshots remain available."),
    "PROJECTION-SPEC.json": ("view transformation specification", "Recorded specification accompanying manager-selected evidence views."),
    "launch-adaptation.json": ("launch adaptation record", "Records a prefix to the initial prompt and identifies the prepared session."),
    "manage.py": ("manager interaction and recording wrapper", "Constructs manager-side command invocations and saves outputs. Recording layouts differ; consult this program's source and review/recording-layouts.csv."),
    "check_captures.py": ("capture verification helper", "Checks saved export identities and recorded comparison membership from preserved inputs."),
    "source_review.py": ("manager static source probe", "Produces an AST-based source summary; the saved output and any later correction remain distinct artifacts."),
}

ASSIGNMENT_SOURCES = ("AUDITOR.md", "MAINTAINER.md", "PROMPT.md", "LAUNCH-PROFILE.json")

HISTORICAL_READING_ORDER = [
    "", "READING ORDER", "1. Recording instructions and narrative, reproduced below with original line numbers.",
    "2. Saved AUDITOR.md, MAINTAINER.md and PROMPT.md at the ASSIGNMENT SOURCE locations below.",
    "3. review/manager-events.txt, review/commands.txt and review/chronology.csv for interactions and recorded times.",
    "4. review/evidence-map.csv for numbered exports, including preserved reports.",
    "5. review/accommodations.csv and review/source.txt for programs and source context.", "",
    "Manager conversation availability is listed below and in review/transcript-map.csv.",
    "The narrative and command records have different coverage. Runs 001 and 003 report Git logging omissions.",
    "Do not treat a missing row as proof that an action did not occur.", ""]

RUN_NOTES = {"run-003": "User reported a new agent for this run during migration; this is attributed context."}

SHARED_TRANSCRIPT = {"run": "run-001;run-002", "source_path": "sol-manager2.jsonl / sol-manager2.md (external copies)",
                     "line": "", "time": "", "record_kind": "shared manager transcript",
                     "message_role": "", "uuid": "", "payload_kind": "", "availability": "not included; user removed ad hoc copies before archiving"}


def stream_kind(parts):
    """Stewart's auditor, watcher and historical manager exports, by their recorded locations."""
    if len(parts) >= 4 and parts[2] == "scratch":
        if parts[-1].startswith("auditor") and parts[-1].endswith(".jsonl"):
            return "external auditor stream"
        if parts[3] == "watcher-events.jsonl":
            return "external watcher event"
    if len(parts) == 3 and parts[2].startswith("sol-manager") and parts[2].endswith(".jsonl"):
        return CONVERSATION
    return ""


def is_conversation_export(path, run):
    return path.startswith(f"runs/{run}/sol-manager")


def assignment_source(path, run):
    return path.startswith(f"runs/{run}/scratch/") and "/audits/" in path and Path(path).name in ASSIGNMENT_SOURCES
