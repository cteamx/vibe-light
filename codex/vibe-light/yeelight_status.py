#!/usr/bin/env python
# encoding:utf-8
"""Hook-driven light strip status controller for AI coding assistants.

The script receives hook events from argv/stdin, aggregates concurrent task
state in a shared state file, and maps the final status to Yeelight effects.
"""

import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from yeelight import Bulb, Flow, RGBTransition, SleepTransition

# Yeelight device address. Change this to your light strip or bulb IP.
BULB_IP = "192.168.3.57"

# State and lock files live in /tmp by default so multiple assistant sessions
# can share one light status.
STATE_PATH = Path(
    os.environ.get("VIBE_LIGHT_STATE")
    or "/tmp/vibe-light-status.json"
)
LOCK_PATH = Path(
    os.environ.get("VIBE_LIGHT_LOCK")
    or "/tmp/vibe-light-status.lock"
)

# Stale task records are pruned so interrupted hooks do not leave the light in
# a permanent "running" state.
TASK_TTL_SECONDS = 8 * 60 * 60

# Hook event groups. Multiple raw events can map to the same light status.
RUNNING_EVENTS = {"thinking", "running"}
WAITING_EVENTS = {"need_approval"}
DONE_EVENTS = {"done"}
RESET_EVENTS = {"reset", "clear"}

# Candidate payload keys that may identify one turn or session.
TASK_ID_KEYS = (
    "turn_id",
    "turnId",
    "session_id",
    "sessionId",
    "conversation_id",
    "conversationId",
    "thread_id",
    "threadId",
    "transcript_path",
    "transcriptPath",
)

# Environment fallbacks when the payload does not include a task id.
TASK_ID_ENV_KEYS = (
    "VIBE_LIGHT_TASK_ID",
    "AI_TURN_ID",
    "AI_SESSION_ID",
    "AI_CONVERSATION_ID",
    "AI_THREAD_ID",
    "CODEX_TURN_ID",
    "CODEX_SESSION_ID",
    "CODEX_CONVERSATION_ID",
    "CODEX_THREAD_ID",
)


def read_hook_payload():
    """Read the JSON payload passed to the hook through stdin."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}

    try:
        raw = sys.stdin.read().strip()
    except Exception:
        return {}

    if not raw:
        return {}

    try:
        payload = json.loads(raw)
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def find_key(obj, wanted_key):
    """Recursively find the first matching key in nested payload data."""
    if isinstance(obj, dict):
        if wanted_key in obj:
            return obj[wanted_key]
        for value in obj.values():
            found = find_key(value, wanted_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, wanted_key)
            if found is not None:
                return found
    return None


def compact_id(value):
    """Return a stable id, hashing long values to keep the state file small."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) <= 180:
        return text
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def get_task_id(payload):
    """Resolve a stable task id from payload, environment, or current cwd."""
    for key in TASK_ID_KEYS:
        value = find_key(payload, key)
        task_id = compact_id(value)
        if task_id:
            return task_id

    for key in TASK_ID_ENV_KEYS:
        task_id = compact_id(os.environ.get(key))
        if task_id:
            return task_id

    cwd = compact_id(find_key(payload, "cwd") or os.environ.get("PWD") or os.getcwd())
    digest = hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:12]
    return "cwd:" + digest


def get_cwd(payload):
    """Extract the current working directory from the hook payload if present."""
    cwd = find_key(payload, "cwd") or os.environ.get("PWD")
    return compact_id(cwd) if cwd else ""


def load_state():
    """Load shared state and normalize missing or invalid fields."""
    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except Exception:
        state = {}

    if not isinstance(state, dict):
        state = {}
    if not isinstance(state.get("tasks"), dict):
        state["tasks"] = {}
    return state


def save_state(state):
    """Atomically write the state file to avoid partial JSON reads."""
    temp_path = STATE_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=True, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp_path, STATE_PATH)


def prune_tasks(tasks, now):
    """Remove task records that have not been updated within the TTL window."""
    expired = []
    for task_id, task in tasks.items():
        updated_at = task.get("updated_at", 0) if isinstance(task, dict) else 0
        if now - float(updated_at or 0) > TASK_TTL_SECONDS:
            expired.append(task_id)
    for task_id in expired:
        tasks.pop(task_id, None)


def resolve_status(tasks):
    """Collapse all active tasks into one global light status."""
    if any(task.get("status") == "need_approval" for task in tasks.values() if isinstance(task, dict)):
        return "need_approval"
    if tasks:
        return "running"
    return "done"


def update_state(event, task_id, payload):
    """Apply one hook event to shared state under an exclusive file lock."""
    now = time.time()

    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        state = load_state()
        tasks = state["tasks"]
        prune_tasks(tasks, now)

        if event in RUNNING_EVENTS:
            tasks[task_id] = {
                "status": "running",
                "updated_at": now,
                "cwd": get_cwd(payload),
            }
        elif event in WAITING_EVENTS:
            task = tasks.get(task_id, {})
            if not isinstance(task, dict):
                task = {}
            task.update(
                {
                    "status": "need_approval",
                    "updated_at": now,
                    "cwd": task.get("cwd") or get_cwd(payload),
                }
            )
            tasks[task_id] = task
        elif event in DONE_EVENTS:
            tasks.pop(task_id, None)
        elif event in RESET_EVENTS:
            tasks.clear()

        previous_status = state.get("last_status")
        current_status = resolve_status(tasks)
        state["last_status"] = current_status
        state["updated_at"] = now
        save_state(state)

        should_apply = current_status != previous_status or event in RESET_EVENTS
        return current_status, should_apply, state


def get_bulb():
    """Create a Yeelight client for the configured light strip or bulb."""
    return Bulb(BULB_IP, auto_on=True)


def stop_effect(bulb):
    """Stop the current flow or music mode before applying a new effect."""
    try:
        bulb.stop_flow()
    except Exception:
        pass
    try:
        bulb.stop_music()
    except Exception:
        pass


def set_solid(bulb, red, green, blue, brightness):
    """Switch the light to a static RGB color at the given brightness."""
    stop_effect(bulb)
    bulb.turn_on()
    bulb.set_rgb(red, green, blue, duration=250)
    bulb.set_brightness(brightness, duration=250)


def start_thinking(bulb):
    """Start the blue-purple breathing effect used while the assistant works."""
    stop_effect(bulb)
    bulb.turn_on()
    flow = Flow(
        count=0,
        action=Flow.actions.recover,
        transitions=[
            RGBTransition(40, 0, 255, duration=900, brightness=25),
            SleepTransition(duration=120),
            RGBTransition(40, 0, 255, duration=900, brightness=100),
            SleepTransition(duration=120),
        ],
    )
    bulb.start_flow(flow)


def apply_light(status):
    """Map the aggregated assistant status to the physical light effect."""
    bulb = get_bulb()
    if status == "running":
        start_thinking(bulb)
    elif status == "need_approval":
        set_solid(bulb, 255, 0, 217, 100)
    elif status == "done":
        set_solid(bulb, 255, 255, 255, 100)


def main():
    """Command entry point for hooks and manual status checks."""
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = read_hook_payload()

    if event == "status":
        state = load_state()
        print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
        return

    if event not in RUNNING_EVENTS | WAITING_EVENTS | DONE_EVENTS | RESET_EVENTS:
        return

    task_id = get_task_id(payload)
    status, should_apply, _ = update_state(event, task_id, payload)
    if should_apply:
        apply_light(status)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
