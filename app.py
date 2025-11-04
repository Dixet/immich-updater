import os
import json
import html
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import quote

from flask import Flask, request, Response, redirect, url_for
from dotenv import load_dotenv

from immich_client import ImmichClient
from utils import compile_patterns, guess_dt_from_filename, to_iso_z, merge_time_from_source


# ---------- .env loading ----------
def _load_env() -> Dict[str, Any]:
    loaded_from = None
    here_env = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(here_env):
        load_dotenv(here_env, override=False)
        loaded_from = here_env
    else:
        load_dotenv()
        loaded_from = os.getenv("_DOTENV_FILE", "auto")

    # Optional patterns: JSON array of [regex, strftime] pairs
    patterns_raw = os.getenv("FILENAME_PATTERNS_JSON", "")
    try:
        patterns_from_env = json.loads(patterns_raw) if patterns_raw else []
    except Exception:
        patterns_from_env = []

    return {
        "IMMICH_BASE_URL": os.getenv("IMMICH_BASE_URL"),
        "IMMICH_API_KEY": os.getenv("IMMICH_API_KEY"),
        "IMMICH_VERIFY_SSL": os.getenv("IMMICH_VERIFY_SSL", "1"),
        "DEFAULT_TAG_NAME": os.getenv("DEFAULT_TAG_NAME", ""),
        "FILENAME_PATTERNS": patterns_from_env,  # list[[regex, fmt], ...]
        "_LOADED_FROM": loaded_from,
    }


CFG = _load_env()
app = Flask(__name__)


def mk_client() -> ImmichClient:
    base_url = CFG.get("IMMICH_BASE_URL") or ""
    api_key = CFG.get("IMMICH_API_KEY") or ""
    verify_ssl = str(CFG.get("IMMICH_VERIFY_SSL", "1")).strip() not in ("0", "false", "False")
    return ImmichClient(base_url=base_url, api_key=api_key, verify_ssl=verify_ssl)


# ---------- tiny HTML helpers ----------
def _page(body: str, title: str = "Immich Updater") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>" + html.escape(title) + "</title>"
        "<style>"
        "body{font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:16px}"
        ".bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}"
        "input[type=text],input[type=number],textarea{padding:6px 8px}"
        "button{padding:6px 10px;cursor:pointer}"
        "table{border-collapse:collapse;width:100%;margin-top:14px}"
        "th,td{border:1px solid #ddd;padding:8px;font-size:14px}"
        "th{background:#f6f6f6;text-align:left}"
        ".muted{color:#666}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
        ".right{text-align:right}.good{color:#0a7}"
        "</style></head><body>"
        + body +
        "</body></html>"
    )


def _asset_row(a: Dict[str, Any], guessed_iso: Optional[str]) -> str:
    asset_id = html.escape(a.get("id", ""))
    fname = html.escape(a.get("originalFileName", ""))
    exif = a.get("exifInfo") or {}
    current_iso = None
    if isinstance(exif, dict):
        dto = exif.get("dateTimeOriginal")
        if dto:
            current_iso = dto if isinstance(dto, str) else str(dto)
    if not current_iso:
        current_iso = a.get("fileCreatedAt") or a.get("localDateTime") or ""
    current_iso = html.escape(current_iso or "")

    guessed_cell = "<span class='muted'>-</span>"
    if guessed_iso:
        guessed_cell = "<span class='good'>" + html.escape(guessed_iso) + "</span>"

    return (
        "<tr>"
        "<td class='mono'>" + asset_id + "</td>"
        "<td>" + fname + "</td>"
        "<td class='mono'>" + current_iso + "</td>"
        "<td class='mono'>" + guessed_cell + "</td>"
        "</tr>"
    )


# ---------- routes ----------
@app.get("/health")
def health() -> Response:
    return Response("OK", status=200)


@app.get("/")
def root() -> Response:
    return redirect(url_for("ui_search"))


@app.get("/whoami")
def whoami() -> Response:
    client = mk_client()
    info = client.whoami()
    payload = {
        "env": {"loaded_from": CFG.get("_LOADED_FROM"), "base_url": CFG.get("IMMICH_BASE_URL")},
        "whoami": info,
    }
    return app.response_class(response=json.dumps(payload, indent=2), status=200, mimetype="application/json")


@app.get("/ui/search")
def ui_search() -> Response:
    # query string
    tag_name = (request.args.get("tag") or CFG.get("DEFAULT_TAG_NAME") or "").strip()
    file_contains = (request.args.get("file") or "").strip()
    limit = int(request.args.get("limit") or "200")
    # patterns: if query param empty, preload from .env
    patterns_text = request.args.get("patterns", "")
    if not patterns_text:
        # show REGEX per line from .env, append ::fmt= if format provided
        env_lines = []
        for pair in (CFG.get("FILENAME_PATTERNS") or []):
            if isinstance(pair, (list, tuple)) and pair:
                regex = str(pair[0])
                # If there's a format in the pair, append it with ::fmt= syntax
                if len(pair) > 1 and pair[1]:
                    regex += f"::fmt={pair[1]}"
                env_lines.append(regex)
        patterns_text = "\n".join(env_lines)
    filter_flag = (request.args.get("filter") or "0") == "1"

    # guard: missing config
    if not CFG.get("IMMICH_BASE_URL") or not CFG.get("IMMICH_API_KEY"):
        body = (
            "<h2>Immich Updater</h2>"
            "<p class='muted'>Loaded from: <span class='mono'>" + html.escape(str(CFG.get('_LOADED_FROM'))) + "</span></p>"
            "<p><strong>Missing configuration.</strong> Set <code>IMMICH_BASE_URL</code> and <code>IMMICH_API_KEY</code> in .env</p>"
        )
        return Response(_page(body), status=200)

    client = mk_client()

    # resolve tag -> uuid
    tag_uuid = ""
    if tag_name:
        tag_uuid = client.get_tag_id_by_name(tag_name) or ""

    # immich search payload: by TAG only; do NOT add filename/pattern filters here
    assets: List[Dict[str, Any]] = []
    debug_payload: Dict[str, Any] = {}
    if tag_uuid:
        assets, debug_payload = client.search_assets_by_tag(
            tag_id=tag_uuid,
            page=1,
            size=limit,
            filename_contains=file_contains or None  # keep this small helper if you want a substring box
        )

    # compile patterns for guessing+frontend filtering
    compiled = compile_patterns(patterns_text) if patterns_text.strip() else []

    # filter AFTER we fetched (OR semantics across lines)
    visible_assets: List[Dict[str, Any]] = []
    for a in assets:
        fname = a.get("originalFileName", "") or ""
        guessed = guess_dt_from_filename(fname, compiled) if compiled else None
        
        # Merge time from current datetime if guessed has no time component
        if guessed:
            # Extract current datetime from asset
            exif = a.get("exifInfo") or {}
            current_dt_str = None
            if isinstance(exif, dict):
                current_dt_str = exif.get("dateTimeOriginal")
            if not current_dt_str:
                current_dt_str = a.get("fileCreatedAt") or a.get("localDateTime") or ""
            
            # Parse current datetime if available
            current_dt = None
            if current_dt_str:
                try:
                    # Try parsing ISO format
                    current_dt = datetime.fromisoformat(str(current_dt_str).replace('Z', '+00:00'))
                except Exception:
                    pass
            
            # Merge time if we have a current datetime
            if current_dt:
                guessed = merge_time_from_source(guessed, current_dt)
        
        a["_guessed_iso"] = to_iso_z(guessed) if guessed else None  # stash for table
        if not filter_flag:
            visible_assets.append(a)
        else:
            # show only those that matched at least one pattern (i.e., have guessed time)
            if a["_guessed_iso"]:
                visible_assets.append(a)

    count_visible = len(visible_assets)

    # top search form (tag/contains/limit) – patterns live in the section below
    tag_esc = html.escape(tag_name, quote=True)
    file_esc = html.escape(file_contains, quote=True)
    ptxt_hidden = html.escape(patterns_text or "", quote=True)
    filter_val = "1" if filter_flag else "0"

    search_form = (
        "<div class='bar'>"
        "<form method='GET' action='/ui/search' class='bar'>"
        "<label>Tag:</label>"
        "<input type='text' name='tag' value='" + tag_esc + "' placeholder='tag name'>"
        "<label>File contains:</label>"
        "<input type='text' name='file' value='" + file_esc + "' placeholder='substring'>"
        "<label>Limit:</label>"
        "<input type='number' name='limit' min='1' max='5000' value='" + str(limit) + "'>"
        "<input type='hidden' name='patterns' value='" + ptxt_hidden + "'>"
        "<input type='hidden' name='filter' value='" + filter_val + "'>"
        "<button type='submit'>Search</button>"
        "</form>"
        "<form method='POST' action='/update-all' onsubmit='return confirm(\"Update ALL visible?\")'>"
        "<input type='hidden' name='tag' value='" + tag_esc + "'>"
        "<input type='hidden' name='file' value='" + file_esc + "'>"
        "<input type='hidden' name='limit' value='" + str(limit) + "'>"
        "<input type='hidden' name='patterns' value='" + ptxt_hidden + "'>"
        "<button type='submit'>Update ALL (" + str(count_visible) + ")</button>"
        "</form>"
        "</div>"
    )

    # patterns section (under “Search debug payload”)
    ptxt_area = html.escape(patterns_text or "", quote=False)
    patterns_section = (
        "<details><summary>Search debug payload</summary>"
        "<pre class='mono'>" + html.escape(json.dumps(debug_payload, indent=2)) + "</pre>"
        "</details>"
        "<p>Patterns (regex; one per line):</p>"
        "<form method='GET' action='/ui/search' class='bar'>"
        "<input type='hidden' name='tag' value='" + tag_esc + "'>"
        "<input type='hidden' name='file' value='" + file_esc + "'>"
        "<input type='hidden' name='limit' value='" + str(limit) + "'>"
        "<textarea name='patterns' rows='5' cols='100'>" + ptxt_area + "</textarea>"
        "<button type='submit' name='filter' value='1'>Filter</button>"
        "<button type='submit' name='filter' value='0'>Clear</button>"
        "</form>"
    )

    # table
    rows = []
    for a in visible_assets:
        rows.append(_asset_row(a, a.get("_guessed_iso")))
    if not rows:
        rows.append("<tr><td colspan='4' class='muted'>No results.</td></tr>")

    table_html = (
        "<table><thead><tr>"
        "<th>ID</th><th>Filename</th><th>Current datetime</th><th>Guessed datetime</th>"
        "</tr></thead><tbody>"
        + "".join(rows) +
        "</tbody></table>"
    )

    header_info = (
        "<h2>Immich Updater</h2>"
        "<p>Base URL: <span class='mono'>" + html.escape(str(CFG.get("IMMICH_BASE_URL"))) + "</span>"
        " · Tag UUID: <span class='mono'>" + html.escape(tag_uuid or "—") + "</span>"
        " · Loaded from: <span class='mono'>" + html.escape(str(CFG.get('_LOADED_FROM'))) + "</span></p>"
        "<p><strong>" + str(count_visible) + "</strong> asset(s) found.</p>"
    )

    body = search_form + header_info + patterns_section + table_html
    return Response(_page(body), status=200)


# ---------- bulk update ----------
@app.post("/update-all")
def update_all() -> Response:
    tag_name = (request.form.get("tag") or "").strip()
    file_contains = (request.form.get("file") or "").strip()
    limit = int(request.form.get("limit") or "200")
    patterns_text = request.form.get("patterns") or ""
    compiled = compile_patterns(patterns_text) if patterns_text.strip() else []

    client = mk_client()
    tag_uuid = client.get_tag_id_by_name(tag_name) if tag_name else None

    updated = 0
    skipped = 0
    errors: List[str] = []

    assets: List[Dict[str, Any]] = []
    debug_payload = {}
    if tag_uuid:
        assets, debug_payload = client.search_assets_by_tag(
            tag_id=tag_uuid, page=1, size=limit, filename_contains=file_contains or None
        )

    for a in assets:
        fname = a.get("originalFileName", "") or ""
        guess_dt = guess_dt_from_filename(fname, compiled) if compiled else None
        if not guess_dt:
            skipped += 1
            continue
        
        # Merge time from current datetime if guessed has no time component
        exif = a.get("exifInfo") or {}
        current_dt_str = None
        if isinstance(exif, dict):
            current_dt_str = exif.get("dateTimeOriginal")
        if not current_dt_str:
            current_dt_str = a.get("fileCreatedAt") or a.get("localDateTime") or ""
        
        # Parse current datetime if available
        if current_dt_str:
            try:
                current_dt = datetime.fromisoformat(str(current_dt_str).replace('Z', '+00:00'))
                guess_dt = merge_time_from_source(guess_dt, current_dt)
            except Exception:
                pass
        
        new_iso = to_iso_z(guess_dt)
        try:
            client.update_asset_datetime(
                a.get("id", ""),
                date_time_original_iso=new_iso,
                file_created_at_iso=new_iso,
                file_modified_at_iso=new_iso,
            )
            # Remove the tag used for search after successful update
            if tag_uuid:
                try:
                    client.remove_tag_from_asset(a.get("id", ""), tag_uuid)
                except Exception as tag_ex:
                    # Log tag removal error but don't fail the update
                    errors.append(f"{a.get('id','?')}: Tag removal failed: {tag_ex}")
            updated += 1
        except Exception as ex:
            errors.append(f"{a.get('id','?')}: {ex}")

    # back to current filters, keep patterns in the box
    back_qs = (
        "tag=" + quote(tag_name, safe="") +
        "&file=" + quote(file_contains, safe="") +
        "&limit=" + str(limit) +
        "&patterns=" + quote(patterns_text, safe="") +
        "&filter=1"
    )

    body = (
        "<h3>Bulk update finished</h3>"
        "<p>Updated: <strong>" + str(updated) + "</strong> · Skipped: <strong>" + str(skipped) + "</strong></p>"
        + ("<h4>Errors</h4><pre class='mono'>" + html.escape("\n".join(errors)) + "</pre>" if errors else "<p>No errors.</p>")
        + "<p><a href='/ui/search?" + back_qs + "'>Back to search</a></p>"
    )
    return Response(_page(body, "Update all"), status=200)


if __name__ == "__main__":
    print("IMMICH_BASE_URL:", CFG.get("IMMICH_BASE_URL"))
    print("Loaded from:", CFG.get("_LOADED_FROM"))
    app.run(host="0.0.0.0", port=5000, debug=True)
