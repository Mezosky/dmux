"""Exec an explicit argv in a tmux window without shell interpolation."""
import os
import sys
import base64
import json


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Missing executable")
    command = json.loads(base64.urlsafe_b64decode(sys.argv[1]))
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise SystemExit("Invalid command arguments")
    os.execvpe(command[0], command, os.environ)
