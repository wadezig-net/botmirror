import json
import os
import threading

HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "upload_history.json",
)
_lock = threading.Lock()


def _load():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_entry(short_id, entry):
    with _lock:
        data = _load()
        data[short_id] = entry
        _save(data)


def get_entry(short_id):
    with _lock:
        return _load().get(short_id)


def remove_entry(short_id):
    with _lock:
        data = _load()
        if short_id in data:
            del data[short_id]
            _save(data)
            return True
        return False


def list_by_user(user_id):
    with _lock:
        data = _load()
        return {k: v for k, v in data.items() if v.get("user_id") == user_id}
