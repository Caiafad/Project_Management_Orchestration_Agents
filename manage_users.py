#!/usr/bin/env python
"""
User management CLI for the Project Management Agent.

Usage:
  python manage_users.py add <username> <password>
  python manage_users.py remove <username>
  python manage_users.py list
  python manage_users.py export          # print USERS_JSON value for Cloud Run

Examples:
  python manage_users.py add alice MySecurePassword123
  python manage_users.py add bob  AnotherPassword456
  python manage_users.py list
  python manage_users.py export   # copy this into Cloud Run USERS_JSON env var
"""

import sys
import json
from auth import add_user, remove_user, load_users


def cmd_add(args):
    if len(args) < 2:
        print("Usage: python manage_users.py add <username> <password>")
        sys.exit(1)
    username, password = args[0], args[1]
    if len(password) < 8:
        print("Error: password must be at least 8 characters.")
        sys.exit(1)
    add_user(username, password)
    print(f"OK User '{username}' added successfully.")


def cmd_remove(args):
    if len(args) < 1:
        print("Usage: python manage_users.py remove <username>")
        sys.exit(1)
    remove_user(args[0])
    print(f"OK User '{args[0]}' removed.")


def cmd_list(args):
    users = load_users()
    if not users:
        print("No users found. Add users with: python manage_users.py add <username> <password>")
        return
    print(f"\n{'USERNAME':<30} STATUS")
    print("-" * 40)
    for username in sorted(users.keys()):
        print(f"  {username:<28} active")
    print(f"\nTotal: {len(users)} user(s)\n")


def cmd_export(args):
    """Print the USERS_JSON value ready to paste into Cloud Run."""
    users = load_users()
    if not users:
        print("No users to export. Add users first.")
        return
    print("\n── Copy this value into Cloud Run → Edit Service → Variables & Secrets ──")
    print(f"\nVariable name : USERS_JSON")
    print(f"Variable value: {json.dumps(users)}")
    print("\n─────────────────────────────────────────────────────────────────────────\n")


COMMANDS = {
    "add":    cmd_add,
    "remove": cmd_remove,
    "list":   cmd_list,
    "export": cmd_export,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
