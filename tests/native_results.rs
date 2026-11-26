//! Read native output recorded from the actual CLIs, reduced to the fields Berman reads.
//!
//! Each sample names the CLI version and situation that produced it. Add a sample when a harness
//! changes the output an unattended sitting reports.

use berman::harness::Harness;
use serde_json::{Value, json};

fn jsonl(events: &Value) -> Vec<u8> {
    events.as_array().unwrap().iter().flat_map(|event| format!("{event}\n").into_bytes()).collect()
}

/// codex-cli 0.171.0, codex exec --json, signed out: the turn fails after reconnection attempts.
fn codex_signed_out() -> Value {
    json!([
        {"type": "thread.started", "thread_id": "01a204d2-9f33-7941-b932-2b13ecac3758"},
        {"type": "turn.started"},
        {"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401 Unauthorized)"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": "Falling back from WebSockets to HTTPS transport."}},
        {"type": "error", "message": "Reconnecting... 5/5 (unexpected status 401 Unauthorized)"},
        {"type": "error", "message": "unexpected status 401 Unauthorized"},
        {"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized"}},
    ])
}

/// codex-cli 0.171.0, codex exec --json: a progress message, two commands, then the final message.
fn codex_completed() -> Value {
    json!([
        {"type": "thread.started", "thread_id": "01a205c0-8c55-7a03-9308-146f8fd19681"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "I’ll inspect the files."}},
        {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "ls -a", "exit_code": null, "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "ls -a", "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "A conversion module."}},
        {"type": "turn.completed", "usage": {}},
    ])
}

/// Claude Code 2.1.330, --print --output-format json, default permissions: an edit was denied.
fn claude_denied() -> Value {
    json!({
        "type": "result",
        "subtype": "success",
        "is_error": false,
        "result": "I didn't fix the bug or run the tests, because both actions were blocked.",
        "session_id": "c2b74254-6c8f-4745-a2b2-f30f4822b6d3",
        "errors": null,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "permission_denials": [{
            "tool_name": "Edit",
            "tool_use_id": "toolu_01PVQW3jnNoSuVh1spPM6UW6",
            "tool_input": {"file_path": "/home/berman-dev/trials/project/convert.py"},
        }],
    })
}

/// Claude Code 2.1.330, --print --output-format json, interrupted with SIGINT. The process exited 0.
fn claude_interrupted() -> Value {
    json!({
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": true,
        "result": null,
        "session_id": "c2b74254-6c8f-4745-a2b2-f30f4822b6d3",
        "errors": ["[ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use"],
        "stop_reason": "tool_use",
        "terminal_reason": "aborted_streaming",
        "permission_denials": [],
    })
}

#[test]
fn codex_failed_turn_reports_its_session_and_one_error() {
    let result = Harness::Codex.outcome(&jsonl(&codex_signed_out()));
    assert_eq!(result.session.as_deref(), Some("01a204d2-9f33-7941-b932-2b13ecac3758"));
    assert_eq!(result.response, None);
    assert_eq!(result.denials, Value::Null);
    assert_eq!(result.limitations, ["The native turn failed: unexpected status 401 Unauthorized."]);
}

#[test]
fn codex_output_cut_off_mid_turn_has_no_response() {
    let events = codex_signed_out();
    let cut = json!(events.as_array().unwrap()[..5]);
    let result = Harness::Codex.outcome(&jsonl(&cut));
    assert_eq!(result.response, None);
    assert_eq!(
        result.limitations,
        [
            "The native output does not show the turn finishing.",
            "Its last reported error: Reconnecting... 5/5 (unexpected status 401 Unauthorized)",
        ]
    );
}

#[test]
fn codex_completed_turn_reports_only_its_final_message() {
    let result = Harness::Codex.outcome(&jsonl(&codex_completed()));
    assert_eq!(result.session.as_deref(), Some("01a205c0-8c55-7a03-9308-146f8fd19681"));
    assert_eq!(result.response.as_deref(), Some("A conversion module."));
    assert!(result.limitations.is_empty());
    let events = codex_completed();
    let unfinished = json!(events.as_array().unwrap()[..events.as_array().unwrap().len() - 1]);
    assert_eq!(Harness::Codex.outcome(&jsonl(&unfinished)).response, None);
}

#[test]
fn claude_result_reports_denials_with_its_response() {
    let sample = claude_denied();
    let result = Harness::Claude.outcome(sample.to_string().as_bytes());
    assert_eq!(result.session.as_deref(), Some("c2b74254-6c8f-4745-a2b2-f30f4822b6d3"));
    assert_eq!(result.response.as_deref(), sample["result"].as_str());
    let input = json!({"file_path": "/home/berman-dev/trials/project/convert.py"});
    assert_eq!(result.denials, json!([{"tool": "Edit", "input": input}]));
    assert!(result.limitations.is_empty());
    // A verbose setting reports the same result as the last item of a list.
    let listed = json!([{"type": "system"}, sample]);
    assert_eq!(Harness::Claude.outcome(listed.to_string().as_bytes()).response, result.response);
}

#[test]
fn claude_interrupted_result_has_no_response() {
    let result = Harness::Claude.outcome(claude_interrupted().to_string().as_bytes());
    assert_eq!(result.response, None);
    assert_eq!(result.denials, json!([]));
    assert_eq!(
        result.limitations,
        ["The native result reports an error: error_during_execution; aborted_streaming; \
          [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use"]
    );
}

#[test]
fn reading_copies_match_the_python_implementation() {
    // The records under tests/golden/python-0.26 hold each transcript beside the reading copy the Python derived.
    let golden = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/python-0.26");
    for (actor, harness) in [("claude", Harness::Claude), ("codex", Harness::Codex)] {
        let parent = golden.join(actor).join("record/telemetry/parent");
        let raw = std::fs::read(parent.join("transcript.jsonl")).unwrap();
        let reading = harness.render(&raw);
        assert_eq!(reading.text, std::fs::read(parent.join("conversation.txt")).unwrap(), "{actor}");
        assert!(reading.malformed.is_empty());
    }
}
