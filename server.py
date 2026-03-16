"""Yahoo Fantasy Basketball MCP Server.

Exposes Yahoo Fantasy Basketball API operations as MCP tools
via FastMCP over stdio.

Security notes:
- All user/model inputs are validated with allowlist regexes before hitting the library
- get_team() objectpath injection is bypassed — we use teams() + Python filtering
- OAuth2 credentials stored at ~/.yahoo-fantasy-mcp/oauth.json with 0600 perms
- store_file=False passed to yahoo_oauth to prevent default secrets.json writes
"""

import json
import logging
import os
import re
import stat
import datetime
from pathlib import Path

from fastmcp import FastMCP
from yahoo_oauth import OAuth2
import yahoo_fantasy_api as yfa

# Silence the yahoo_oauth DEBUG logger
logging.getLogger('yahoo_oauth').setLevel(logging.WARNING)

# --- Constants ---
CREDS_DIR = Path.home() / ".yahoo-fantasy-mcp"
CREDS_FILE = CREDS_DIR / "oauth.json"
SPORT_CODE = "nba"

# --- Input validation regexes (allowlists) ---
LEAGUE_ID_RE = re.compile(r"^\d{3}\.l\.\d+$")
TEAM_KEY_RE = re.compile(r"^\d{3}\.l\.\d+\.t\.\d+$")
TRANSACTION_KEY_RE = re.compile(r"^\d{3}\.l\.\d+\.(tr|pt)\.\d+$")
POSITION_RE = re.compile(r"^(?:[A-Z]{1,4}|Util)$")
STATUS_VALUES = frozenset({"A", "FA", "W", "T", "K"})
TRAN_TYPES = frozenset({"add", "drop", "commish", "trade"})
REQ_TYPE_VALUES = frozenset({"season", "average_season", "lastweek", "lastmonth", "date", "week"})
TEAM_NAME_RE = re.compile(r"^[\w\s\-'\.!&\(\)#,]{1,100}$")
PLAYER_SEARCH_RE = re.compile(r"^[\w\s\-'\.]{1,64}$")


def validate_league_id(league_id: str) -> str:
    if not LEAGUE_ID_RE.match(league_id):
        raise ValueError(f"Invalid league_id format: {league_id!r}")
    return league_id


def validate_team_key(team_key: str) -> str:
    if not TEAM_KEY_RE.match(team_key):
        raise ValueError(f"Invalid team_key format: {team_key!r}")
    return team_key


def validate_transaction_key(key: str) -> str:
    if not TRANSACTION_KEY_RE.match(key):
        raise ValueError(f"Invalid transaction_key format: {key!r}")
    return key


def validate_position(position: str) -> str:
    if not POSITION_RE.match(position):
        raise ValueError(f"Invalid position format: {position!r}")
    return position


def validate_player_id(pid: int) -> int:
    if not isinstance(pid, int) or pid < 1 or pid > 99999:
        raise ValueError(f"Invalid player_id: {pid!r}")
    return pid


def validate_player_ids(pids: list[int]) -> list[int]:
    return [validate_player_id(p) for p in pids]


def validate_team_name(name: str) -> str:
    if not TEAM_NAME_RE.match(name):
        raise ValueError(f"Invalid team name: {name!r}")
    return name


def validate_player_search(search: str) -> str:
    if not PLAYER_SEARCH_RE.match(search):
        raise ValueError(f"Invalid player search string: {search!r}")
    return search


def sanitize_trade_note(note: str) -> str:
    # Strip control chars, limit length
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", note)
    return cleaned[:500]


# --- Credential management ---
def _ensure_creds_dir():
    """Create creds dir with owner-only permissions."""
    CREDS_DIR.mkdir(mode=0o700, exist_ok=True)


def save_credentials(data: dict):
    """Write OAuth credentials with restricted file permissions."""
    _ensure_creds_dir()
    CREDS_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(CREDS_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600


def load_credentials() -> dict | None:
    """Load OAuth credentials from secure file."""
    if not CREDS_FILE.exists():
        return None
    return json.loads(CREDS_FILE.read_text())


# --- OAuth2 session ---
def get_oauth_session() -> OAuth2:
    """Initialize Yahoo OAuth2 session.

    Credentials must exist at ~/.yahoo-fantasy-mcp/oauth.json.
    Run `python setup_auth.py` first to perform the initial OAuth flow.
    """
    creds = load_credentials()
    if creds is None:
        raise RuntimeError(
            "No OAuth credentials found. Run `python setup_auth.py` in the "
            "project directory to complete the Yahoo OAuth2 flow first."
        )
    # Write a temp file for yahoo_oauth to read (it requires from_file)
    # We use store_file=False to prevent it from writing its own secrets.json
    _ensure_creds_dir()
    temp_creds_file = CREDS_DIR / "_temp_oauth.json"
    temp_creds_file.write_text(json.dumps(creds, indent=2))
    os.chmod(temp_creds_file, stat.S_IRUSR | stat.S_IWUSR)

    try:
        sc = OAuth2(
            None, None,
            from_file=str(temp_creds_file),
            store_file=False,
            browser_callback=False,
            callback_uri="https://localhost",
        )
    finally:
        # Clean up temp file
        temp_creds_file.unlink(missing_ok=True)

    # Persist any refreshed tokens back to our secure store
    token_data = {
        "consumer_key": sc.consumer_key,
        "consumer_secret": sc.consumer_secret,
        "access_token": sc.access_token,
        "token_type": getattr(sc, "token_type", "bearer"),
        "refresh_token": sc.refresh_token,
        "token_time": sc.token_time,
    }
    save_credentials(token_data)

    return sc


# --- Global state (initialized on first tool call) ---
_sc = None
_game = None


def _get_game():
    """Initialize Yahoo Fantasy game API, with error handling.

    Refreshes OAuth session if token has expired (3600s TTL).
    This ensures the session stays valid for long-running CC sessions.
    """
    global _sc, _game
    # Reinitialize if no session, or if token has expired
    if _game is None or (_sc is not None and not _sc.token_is_valid()):
        try:
            _sc = get_oauth_session()
            _game = yfa.Game(_sc, SPORT_CODE)
        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize Yahoo Fantasy API: {type(e).__name__}: {str(e)[:150]}"
            ) from e
    return _game


def _get_league(league_id: str):
    """Fetch league with error handling."""
    validate_league_id(league_id)
    try:
        game = _get_game()
        return game.to_league(league_id)
    except Exception as e:
        if isinstance(e, ValueError):
            raise  # Let validation errors through
        raise RuntimeError(
            f"Failed to fetch league {league_id}: {type(e).__name__}: {str(e)[:150]}"
        ) from e


def _get_team(league_id: str, team_key: str):
    """Fetch team with error handling."""
    validate_league_id(league_id)
    validate_team_key(team_key)
    try:
        lg = _get_league(league_id)
        return lg.to_team(team_key)
    except Exception as e:
        if isinstance(e, ValueError):
            raise  # Let validation errors through
        raise RuntimeError(
            f"Failed to fetch team {team_key}: {type(e).__name__}: {str(e)[:150]}"
        ) from e


# --- MCP Server ---
mcp = FastMCP("yahoo-fantasy-basketball")


# ---- Read-only tools ----

@mcp.tool()
def get_game_id() -> str:
    """Get the Yahoo game ID for the current NBA season."""
    return _get_game().game_id()


@mcp.tool()
def list_leagues(seasons: list[str] | None = None) -> list[str]:
    """List all NBA league IDs for the authenticated user.

    Args:
        seasons: Optional list of season years to filter (e.g. ["2025"]).
    """
    return _get_game().league_ids(
        game_codes=["nba"],
        seasons=seasons,
    )


@mcp.tool()
def get_standings(league_id: str) -> list[dict]:
    """Get league standings.

    Args:
        league_id: Yahoo league ID (e.g. "418.l.12345").
    """
    lg = _get_league(league_id)
    return lg.standings()


@mcp.tool()
def get_teams(league_id: str) -> dict:
    """Get all teams in a league with their details.

    Args:
        league_id: Yahoo league ID (e.g. "418.l.12345").
    """
    lg = _get_league(league_id)
    return lg.teams()


@mcp.tool()
def find_team_by_name(league_id: str, team_name: str) -> dict | None:
    """Find a team by name in a league.

    Bypasses the library's objectpath query to avoid injection.
    Returns team details dict or None if not found.

    Args:
        league_id: Yahoo league ID.
        team_name: Team name to search for (case-insensitive partial match).
    """
    validate_team_name(team_name)
    lg = _get_league(league_id)
    teams = lg.teams()
    search_lower = team_name.lower()
    for key, team in teams.items():
        if search_lower in team.get("name", "").lower():
            return {"team_key": key, **team}
    return None


@mcp.tool()
def get_roster(team_key: str, week: int | None = None, day: str | None = None) -> list[dict]:
    """Get a team's roster.

    Args:
        team_key: Yahoo team key (e.g. "418.l.12345.t.1").
        week: Optional week number.
        day: Optional date string (YYYY-MM-DD).
    """
    validate_team_key(team_key)
    league_id = team_key.split(".t.")[0]
    tm = _get_team(league_id, team_key)
    day_obj = datetime.date.fromisoformat(day) if day else None
    return tm.roster(week=week, day=day_obj)


@mcp.tool()
def get_matchups(league_id: str, week: int | None = None) -> dict:
    """Get matchups for a given week. Defaults to current week.

    Args:
        league_id: Yahoo league ID.
        week: Optional week number.
    """
    lg = _get_league(league_id)
    return lg.matchups(week=week)


@mcp.tool()
def get_current_week(league_id: str) -> int:
    """Get the current week number for a league.

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.current_week()


@mcp.tool()
def get_free_agents(league_id: str, position: str) -> list[dict]:
    """Get free agents for a given position.

    Args:
        league_id: Yahoo league ID.
        position: Position code (e.g. "PG", "SG", "SF", "PF", "C", "G", "F", "Util").
    """
    validate_position(position)
    lg = _get_league(league_id)
    return lg.free_agents(position)


@mcp.tool()
def get_player_details(
    league_id: str,
    player_name: str | None = None,
    player_ids: list[int] | None = None,
) -> list[dict]:
    """Look up player details by name search or player IDs.

    Args:
        league_id: Yahoo league ID.
        player_name: Search string for player name (partial match, max 25 results).
        player_ids: List of Yahoo player IDs to look up.
    """
    lg = _get_league(league_id)
    if player_name is not None:
        validate_player_search(player_name)
        return lg.player_details(player_name)
    elif player_ids is not None:
        validate_player_ids(player_ids)
        return lg.player_details(player_ids)
    else:
        raise ValueError("Provide either player_name or player_ids.")


@mcp.tool()
def get_player_stats(
    league_id: str,
    player_ids: list[int],
    req_type: str = "season",
    date: str | None = None,
    week: int | None = None,
    season: int | None = None,
) -> list[dict]:
    """Get stats for a list of players.

    Args:
        league_id: Yahoo league ID.
        player_ids: List of Yahoo player IDs.
        req_type: One of: season, average_season, lastweek, lastmonth, date, week.
        date: Date string (YYYY-MM-DD) when req_type is "date".
        week: Week number when req_type is "week".
        season: Season year when req_type is "season" or "average_season".
    """
    if req_type not in REQ_TYPE_VALUES:
        raise ValueError(f"Invalid req_type: {req_type!r}. Must be one of {REQ_TYPE_VALUES}")
    validate_player_ids(player_ids)
    lg = _get_league(league_id)
    date_obj = datetime.date.fromisoformat(date) if date else None
    return lg.player_stats(player_ids, req_type, date=date_obj, week=week, season=season)


@mcp.tool()
def get_league_settings(league_id: str) -> dict:
    """Get league settings (scoring type, roster positions, trade rules, etc.).

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.settings()


@mcp.tool()
def get_stat_categories(league_id: str) -> list[dict]:
    """Get the stat categories used for scoring in a league.

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.stat_categories()


@mcp.tool()
def get_league_positions(league_id: str) -> dict:
    """Get the roster positions used in a league.

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.positions()


@mcp.tool()
def get_draft_results(league_id: str) -> list[dict]:
    """Get draft results for a league.

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.draft_results()


@mcp.tool()
def get_transactions(
    league_id: str,
    tran_types: str = "add,drop,trade",
    count: int = 25,
) -> list[dict]:
    """Get recent transactions in a league.

    Args:
        league_id: Yahoo league ID.
        tran_types: Comma-separated types: add,drop,commish,trade.
        count: Number of transactions to return.
    """
    # Validate each transaction type
    for t in tran_types.split(","):
        t = t.strip()
        if t not in TRAN_TYPES:
            raise ValueError(f"Invalid transaction type: {t!r}")
    lg = _get_league(league_id)
    return lg.transactions(tran_types, str(count))


@mcp.tool()
def get_proposed_trades(league_id: str, team_key: str) -> list[dict]:
    """Get all proposed trades involving your team.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
    """
    tm = _get_team(league_id, team_key)
    return tm.proposed_trades()


@mcp.tool()
def get_my_team_key(league_id: str) -> str:
    """Get the team key for the authenticated user in a league.

    Args:
        league_id: Yahoo league ID.
    """
    lg = _get_league(league_id)
    return lg.team_key()


@mcp.tool()
def get_team_details(team_key: str) -> dict:
    """Get details about a specific team.

    Args:
        team_key: Yahoo team key (e.g. "418.l.12345.t.1").
    """
    validate_team_key(team_key)
    league_id = team_key.split(".t.")[0]
    tm = _get_team(league_id, team_key)
    return tm.details()


@mcp.tool()
def get_percent_owned(league_id: str, player_ids: list[int]) -> list[dict]:
    """Get ownership percentage for a list of players.

    Args:
        league_id: Yahoo league ID.
        player_ids: List of Yahoo player IDs.
    """
    validate_player_ids(player_ids)
    lg = _get_league(league_id)
    return lg.percent_owned(player_ids)


@mcp.tool()
def get_player_ownership(league_id: str, player_ids: list[int]) -> dict:
    """Get ownership status (which team owns each player).

    Args:
        league_id: Yahoo league ID.
        player_ids: List of Yahoo player IDs.
    """
    validate_player_ids(player_ids)
    lg = _get_league(league_id)
    return lg.ownership(player_ids)


# ---- Write tools (mutating operations) ----

@mcp.tool()
def add_player(league_id: str, team_key: str, player_id: int) -> str:
    """Add a free agent player to your team.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        player_id: Yahoo player ID of the player to add.
    """
    validate_player_id(player_id)
    tm = _get_team(league_id, team_key)
    tm.add_player(player_id)
    return f"Successfully added player {player_id}."


@mcp.tool()
def drop_player(league_id: str, team_key: str, player_id: int) -> str:
    """Drop a player from your team.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        player_id: Yahoo player ID of the player to drop.
    """
    validate_player_id(player_id)
    tm = _get_team(league_id, team_key)
    tm.drop_player(player_id)
    return f"Successfully dropped player {player_id}."


@mcp.tool()
def add_and_drop_players(
    league_id: str,
    team_key: str,
    add_player_id: int,
    drop_player_id: int,
) -> str:
    """Add one player and drop another in a single transaction.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        add_player_id: Yahoo player ID to add.
        drop_player_id: Yahoo player ID to drop.
    """
    validate_player_id(add_player_id)
    validate_player_id(drop_player_id)
    tm = _get_team(league_id, team_key)
    tm.add_and_drop_players(add_player_id, drop_player_id)
    return f"Successfully added {add_player_id} and dropped {drop_player_id}."


@mcp.tool()
def claim_player(
    league_id: str,
    team_key: str,
    player_id: int,
    faab: int | None = None,
) -> str:
    """Submit a waiver claim for a player.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        player_id: Yahoo player ID to claim.
        faab: Optional FAAB bid amount.
    """
    validate_player_id(player_id)
    tm = _get_team(league_id, team_key)
    tm.claim_player(player_id, faab=faab)
    return f"Waiver claim submitted for player {player_id}."


@mcp.tool()
def change_positions(
    league_id: str,
    team_key: str,
    date: str,
    lineup: list[dict],
) -> str:
    """Change starting positions for players in your lineup.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        date: Start date for the changes (YYYY-MM-DD).
        lineup: List of dicts with keys: player_id (int), selected_position (str, e.g., "PG", "Util").
    """
    tm = _get_team(league_id, team_key)
    date_obj = datetime.date.fromisoformat(date)
    # Validate each lineup entry
    for entry in lineup:
        validate_player_id(entry["player_id"])
        validate_position(entry["selected_position"])
    tm.change_positions(date_obj, lineup)
    return f"Lineup positions updated for {date}."


@mcp.tool()
def propose_trade(
    league_id: str,
    team_key: str,
    tradee_team_key: str,
    your_player_keys: list[str],
    their_player_keys: list[str],
    trade_note: str = "",
) -> str:
    """Propose a trade to another team.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        tradee_team_key: The other team's key.
        your_player_keys: List of your player keys to offer (format: "418.p.5123").
        their_player_keys: List of their player keys you want (format: "418.p.5456").
        trade_note: Optional note (max 500 chars).
    """
    validate_team_key(tradee_team_key)
    tm = _get_team(league_id, team_key)
    note = sanitize_trade_note(trade_note)
    tm.propose_trade(tradee_team_key, your_player_keys, their_player_keys, note)
    return "Trade proposed successfully."


@mcp.tool()
def accept_trade(
    league_id: str,
    team_key: str,
    transaction_key: str,
    trade_note: str = "",
) -> str:
    """Accept a proposed trade.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        transaction_key: The transaction key of the trade to accept.
        trade_note: Optional note.
    """
    validate_transaction_key(transaction_key)
    tm = _get_team(league_id, team_key)
    note = sanitize_trade_note(trade_note)
    tm.accept_trade(transaction_key, note)
    return "Trade accepted."


@mcp.tool()
def reject_trade(
    league_id: str,
    team_key: str,
    transaction_key: str,
    trade_note: str = "",
) -> str:
    """Reject a proposed trade.

    Args:
        league_id: Yahoo league ID.
        team_key: Your team key.
        transaction_key: The transaction key of the trade to reject.
        trade_note: Optional note.
    """
    validate_transaction_key(transaction_key)
    tm = _get_team(league_id, team_key)
    note = sanitize_trade_note(trade_note)
    tm.reject_trade(transaction_key, note)
    return "Trade rejected."


if __name__ == "__main__":
    mcp.run()
