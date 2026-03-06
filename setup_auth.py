"""Interactive setup script for Yahoo OAuth2 credentials.

Run this ONCE before starting the MCP server. It will:
1. Ask for your Yahoo app consumer key and secret
2. Open a browser for Yahoo OAuth authorization
3. Store credentials securely at ~/.yahoo-fantasy-mcp/oauth.json (0600 perms)

Prerequisites:
- Create a Yahoo Developer app at https://developer.yahoo.com/apps/
- Homepage URL: https://localhost
- Redirect URI: https://localhost
- Note your Consumer Key and Consumer Secret
"""

import json
import os
import stat
import sys
import time
from pathlib import Path

from yahoo_oauth import OAuth2

CREDS_DIR = Path.home() / ".yahoo-fantasy-mcp"
CREDS_FILE = CREDS_DIR / "oauth.json"


def main():
    print("=== Yahoo Fantasy MCP — OAuth2 Setup ===\n")

    if CREDS_FILE.exists():
        resp = input(f"Credentials already exist at {CREDS_FILE}. Overwrite? [y/N]: ")
        if resp.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    consumer_key = input("Enter Yahoo Consumer Key: ").strip()
    consumer_secret = input("Enter Yahoo Consumer Secret: ").strip()

    if not consumer_key or not consumer_secret:
        print("Error: Both consumer key and secret are required.")
        sys.exit(1)

    # Write temporary creds file for yahoo_oauth init
    CREDS_DIR.mkdir(mode=0o700, exist_ok=True)
    temp_file = CREDS_DIR / "_setup_temp.json"
    temp_data = {
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
    }
    temp_file.write_text(json.dumps(temp_data, indent=2))
    os.chmod(temp_file, stat.S_IRUSR | stat.S_IWUSR)

    print("\nA browser window will open for Yahoo authorization.")
    print("After authorizing, Yahoo will redirect to https://localhost?code=XXXXX")
    print("The page will fail to load — that's expected.")
    print("Copy the 'code' value from the URL bar and paste it below.\n")

    try:
        sc = OAuth2(
            consumer_key,
            consumer_secret,
            from_file=str(temp_file),
            store_file=False,
            browser_callback=True,
            callback_uri="https://localhost",
        )
    except Exception as e:
        print(f"\nError during OAuth flow: {e}")
        temp_file.unlink(missing_ok=True)
        sys.exit(1)
    finally:
        temp_file.unlink(missing_ok=True)

    # Save credentials securely
    token_data = {
        "consumer_key": sc.consumer_key,
        "consumer_secret": sc.consumer_secret,
        "access_token": sc.access_token,
        "token_type": getattr(sc, "token_type", "bearer"),
        "refresh_token": sc.refresh_token,
        "token_time": sc.token_time,
    }
    CREDS_FILE.write_text(json.dumps(token_data, indent=2))
    os.chmod(CREDS_FILE, stat.S_IRUSR | stat.S_IWUSR)

    print(f"\nCredentials saved to {CREDS_FILE} (permissions: 0600)")
    print("You can now configure the MCP server in ~/.claude/mcp.json")


if __name__ == "__main__":
    main()
