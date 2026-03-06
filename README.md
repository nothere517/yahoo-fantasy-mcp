# Yahoo Fantasy Basketball MCP Server

An MCP (Model Context Protocol) server that connects Claude to your Yahoo Fantasy Basketball leagues. Manage your roster, check stats, analyze matchups, browse free agents, propose trades, and more — all through natural conversation.

## What It Does

This server exposes 29 tools that let Claude interact with the Yahoo Fantasy Basketball API on your behalf:

**Read-only (20 tools):** View your leagues, rosters, standings, matchups, player stats, free agents, draft results, transactions, and more.

**Write (9 tools):** Add/drop players, submit waiver claims, set lineups, propose/accept/reject trades.

## Prerequisites

- **Python 3.10+** installed on your machine
- A **Yahoo account** with at least one Fantasy Basketball league
- **Claude Code** (CLI) and/or **Claude Desktop**

## Setup Guide

### Step 1: Clone the repo

```bash
git clone https://github.com/afong3/yahoo-fantasy-mcp.git
cd yahoo-fantasy-mcp
```

### Step 2: Create a Python virtual environment

```bash
python3 -m venv .venv
```

Activate it:

- **macOS/Linux:** `source .venv/bin/activate`
- **Windows:** `.venv\Scripts\activate`

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Create a Yahoo Developer App

1. Go to [https://developer.yahoo.com/apps/](https://developer.yahoo.com/apps/)
2. Click **Create an App**
3. Fill in:
   - **Application Name:** anything you want (e.g. "Fantasy MCP")
   - **Application Type:** Web Application
   - **Homepage URL:** `https://localhost`
   - **Redirect URI(s):** `https://localhost`
   - **API Permissions:** check **Fantasy Sports** (Read/Write)
4. Click **Create App**
5. Note your **Client ID (Consumer Key)** and **Client Secret (Consumer Secret)** — you'll need them next

### Step 5: Authenticate with Yahoo

Run the setup script:

```bash
python setup_auth.py
```

It will:
1. Ask for your Consumer Key and Consumer Secret
2. Open a browser window to Yahoo's authorization page
3. After you click **Agree**, Yahoo redirects to `https://localhost?code=XXXXX`
4. **The page will fail to load — that's expected.** Copy the `code` value from the URL bar
5. The script captures the code and saves your OAuth tokens

Your credentials are stored at `~/.yahoo-fantasy-mcp/oauth.json` with restricted file permissions (owner-only read/write).

### Step 6: Connect to Claude

#### Claude Code (CLI)

Run this command (adjust the path to where you cloned the repo):

```bash
claude mcp add -s user yahoo-fantasy-basketball -- /path/to/yahoo-fantasy-mcp/.venv/bin/python /path/to/yahoo-fantasy-mcp/server.py
```

For example, if you cloned to your home directory:

```bash
claude mcp add -s user yahoo-fantasy-basketball -- ~/yahoo-fantasy-mcp/.venv/bin/python ~/yahoo-fantasy-mcp/server.py
```

Restart Claude Code, and the tools will be available.

#### Claude Desktop

Edit the config file at:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add the `mcpServers` block (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "yahoo-fantasy-basketball": {
      "command": "/path/to/yahoo-fantasy-mcp/.venv/bin/python",
      "args": [
        "/path/to/yahoo-fantasy-mcp/server.py"
      ]
    }
  }
}
```

Replace `/path/to/yahoo-fantasy-mcp` with the actual path where you cloned the repo.

If you already have other MCP servers configured, just add the `yahoo-fantasy-basketball` entry inside the existing `mcpServers` object.

Restart Claude Desktop. You should see a hammer icon in the chat input confirming the tools are loaded.

## Usage

Once connected, just talk to Claude naturally:

- "What are my fantasy basketball leagues?"
- "Show me my roster"
- "How are my players doing this week?"
- "Check the waiver wire for centers"
- "What are the standings?"
- "Drop Moussa Diabate and pick up Wendell Carter Jr."
- "Propose a trade sending Tari Eason for Herbert Jones"

Claude will use the appropriate tools automatically.

## Tools Reference

| Tool | Description |
|------|-------------|
| `get_game_id` | Get the current NBA season game ID |
| `list_leagues` | List your NBA leagues (optionally filter by season) |
| `get_standings` | Get league standings |
| `get_teams` | Get all teams in a league |
| `find_team_by_name` | Search for a team by name |
| `get_roster` | Get a team's roster (optionally by week or date) |
| `get_matchups` | Get matchups for a given week |
| `get_current_week` | Get the current week number |
| `get_free_agents` | Get free agents by position |
| `get_player_details` | Look up players by name or ID |
| `get_player_stats` | Get player stats (season, weekly, daily, etc.) |
| `get_league_settings` | Get league scoring and roster settings |
| `get_stat_categories` | Get stat categories used for scoring |
| `get_league_positions` | Get roster position slots |
| `get_draft_results` | Get draft results |
| `get_transactions` | Get recent league transactions |
| `get_my_team_key` | Get your team key in a league |
| `get_team_details` | Get details about a specific team |
| `get_percent_owned` | Get ownership percentage for players |
| `get_player_ownership` | Get which team owns specific players |
| `add_player` | Add a free agent to your team |
| `drop_player` | Drop a player from your team |
| `add_and_drop_players` | Add and drop in a single transaction |
| `claim_player` | Submit a waiver claim (with optional FAAB bid) |
| `change_positions` | Set your lineup positions |
| `propose_trade` | Propose a trade to another team |
| `accept_trade` | Accept a pending trade |
| `reject_trade` | Reject a pending trade |
| `get_proposed_trades` | View pending trades for your team |

## Troubleshooting

**"No OAuth credentials found"**
You need to run `python setup_auth.py` first. See Step 5 above.

**Token expired / 401 errors**
The server automatically refreshes tokens, but if your refresh token has expired (Yahoo tokens last ~2 weeks of inactivity), re-run `python setup_auth.py` to re-authenticate.

**Tools not showing up in Claude Desktop**
Make sure the paths in `claude_desktop_config.json` are absolute paths (not relative like `~/`). Restart Claude Desktop after editing the config.

**"Invalid league_id format"**
League IDs look like `418.l.12345`. You can find yours by asking Claude to "list my leagues" first.

## Security

- OAuth credentials are stored at `~/.yahoo-fantasy-mcp/oauth.json` with 0600 permissions (owner read/write only)
- All inputs are validated with allowlist regexes before hitting the Yahoo API
- Known objectpath injection vulnerability in the upstream library is mitigated (bypassed entirely)
- Trade notes are sanitized (control characters stripped, 500 char limit)
- `store_file=False` prevents the Yahoo OAuth library from writing credentials to the project directory

## License

MIT
