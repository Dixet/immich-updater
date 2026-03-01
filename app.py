"""Web interface for tagging and timestamp updates.

This application connects to an Immich server, allows selecting assets
via a tag name, and provides utilities to guess new EXIF timestamps
based on filename patterns.  The user can then bulk-update matched
assets and optionally remove the search tag once an update completes.

Configuration is driven by environment variables (often loaded from a
`.env` file).  Helper functions below handle HTML rendering, pattern
compilation, and interaction with the Immich API implemented in
`immich_client.py`.
"""

import os
import json
import html
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import quote

from flask import Flask, request, Response, redirect, url_for
from dotenv import load_dotenv

from immich_client import ImmichClient
from utils import compile_patterns, to_iso_z, merge_time_from_source, guess_info_from_filename


# ---------- .env loading ----------
def _load_env() -> Dict[str, Any]:
    """Load configuration values from a .env file or the environment.

    Returns a dictionary with keys used throughout the application.  If a
    `.env` file exists next to the script it is loaded explicitly; otherwise
    `load_dotenv()` is called with no arguments, allowing the library to find
    the file automatically.  `FILENAME_PATTERNS_JSON` is parsed as JSON
    and returned as a list of pairs if present.
    """
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
    """Create an `ImmichClient` instance from current configuration.

    The boolean `verify_ssl` flag is interpreted loosely so that
    "false", "0", and an unset value all result in SSL verification
    being disabled.
    """
    base_url = CFG.get("IMMICH_BASE_URL") or ""
    api_key = CFG.get("IMMICH_API_KEY") or ""
    verify_ssl = str(CFG.get("IMMICH_VERIFY_SSL", "1")).strip() not in ("0", "false", "False")
    return ImmichClient(base_url=base_url, api_key=api_key, verify_ssl=verify_ssl)


# ---------- tiny HTML helpers ----------
def _page(body: str, title: str = "Immich Updater") -> str:
    """Embed the given HTML `body` inside a minimal page template.

    The returned string includes `<head>` tags, a UTF‑8 charset, a
    title, and some inline CSS for basic styling.  This helper keeps the
    route functions small by centralizing HTML boilerplate.
    """
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


def _asset_row(a: Dict[str, Any], guessed_iso: Optional[str], guessed_descr: Optional[str]) -> str:
    """Return a single asset formatted as a table row.

    The function extracts the asset ID, original filename, current
    timestamp and any guessed timestamp (from the filename) and escapes
    them for inclusion in HTML.
    """
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

    # extract description from common locations
    # Immich sometimes stores descriptions at the asset top-level or
    # inside `exifInfo` under different keys (e.g. `description`,
    # `imageDescription`, `ImageDescription`). Check a few fallbacks.
    desc = ""
    # 1) asset top-level field
    d = a.get("description")
    if not d and isinstance(exif, dict):
        # 2) common EXIF fields that might contain a description
        for key in ("description", "imageDescription", "ImageDescription", "userComment"):
            if key in exif and exif.get(key):
                d = exif.get(key)
                break
    if d:
        desc = html.escape(d if isinstance(d, str) else str(d))
    else:
        desc = "<span class='muted'>-</span>"

    guessed_descr_cell = "<span class='muted'>-</span>"
    if guessed_descr:
        guessed_descr_cell = "<span class='good'>" + html.escape(guessed_descr) + "</span>"

    guessed_cell = "<span class='muted'>-</span>"
    if guessed_iso:
        guessed_cell = "<span class='good'>" + html.escape(guessed_iso) + "</span>"

    # determine source marker
    source = a.get("_source") or "-"
    source_cell = html.escape(source)

    return (
        "<tr>"
        "<td class='mono'>" + asset_id + "</td>"
        "<td>" + fname + "</td>"
        "<td class='mono'>" + current_iso + "</td>"
        "<td class='mono'>" + guessed_cell + "</td>"
        "<td>" + source_cell + "</td>"
        "<td>" + desc + "</td>"
        "<td>" + guessed_descr_cell + "</td>"
        "</tr>"
    )


# ---------- routes ----------
@app.get("/health")
def health() -> Response:
    """Health check endpoint that returns an OK response.

    External services or simple monitors can hit this URL to verify the
    server is running.
    """
    return Response("OK", status=200)


@app.get("/")
def root() -> Response:
    """Root route; simply redirect users to the search interface."""
    return redirect(url_for("ui_search"))


@app.get("/whoami")
def whoami() -> Response:
    """Return JSON about the current environment and API connectivity.

    This helper is useful for debugging configuration issues; it invokes
    the `whoami` helper on the Immich client which performs a lightweight
    `GET /api/tags` call and reports success or failure.
    """
    client = mk_client()
    info = client.whoami()
    payload = {
        "env": {"loaded_from": CFG.get("_LOADED_FROM"), "base_url": CFG.get("IMMICH_BASE_URL")},
        "whoami": info,
    }
    return app.response_class(response=json.dumps(payload, indent=2), status=200, mimetype="application/json")


@app.get("/ui/search")
def ui_search() -> Response:
    """Render the search form and optionally a table of matching assets.

    Reads query parameters (tag, file substring, limit, patterns, filter
    flag) and uses the Immich API to fetch assets tagged accordingly.  If
    filename patterns are supplied we attempt to guess a datetime for each
    file and merge time components from any existing metadata.  The
    results are displayed in HTML with the ability to trigger a bulk
    update.
    """
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

    # keep raw patterns for passing through forms (escaped later)

    # guard: missing config
    if not CFG.get("IMMICH_BASE_URL") or not CFG.get("IMMICH_API_KEY"):
        body = (
            "<h2>Immich Updater</h2>"
            "<p class='muted'>Loaded from: <span class='mono'>" + html.escape(str(CFG.get('_LOADED_FROM'))) + "</span></p>"
            "<p><strong>Missing configuration.</strong> Set <code>IMMICH_BASE_URL</code> and <code>IMMICH_API_KEY</code> in .env</p>"
        )
        return Response(_page(body), status=200)

    client = mk_client()

    # If the query includes load_exif_id we will fetch the detailed asset
    # when rendering (see below). The per-row Load EXIF form posts to
    # `/ui/load-exif` which redirects back to this route with that param.
    # We only store the value here so the loop below can act on it.
    # (default is None)
    # load_exif_id = request.args.get("load_exif_id")

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

        # Fetch detailed asset info (per-asset) to obtain full EXIF and
        # description fields that the search/metadata endpoint may omit.
        # We merge the detailed response into the original asset dict so
        # downstream logic can read `exifInfo` and `description` directly.
        for a in assets:
            asset_id = a.get("id") or ""
            if not asset_id:
                continue
            try:
                detailed = client.get_asset(asset_id)
                if isinstance(detailed, dict):
                    # merge but keep original keys if detailed is missing them
                    a.update(detailed)
            except Exception as ex:
                # Preserve error info for debugging; continue with the
                # less-detailed asset returned by the search endpoint.
                a["_detail_error"] = str(ex)

    # compile patterns for guessing+frontend filtering
    compiled = compile_patterns(patterns_text) if patterns_text.strip() else []

    # filter AFTER we fetched (OR semantics across lines)
    visible_assets: List[Dict[str, Any]] = []
    for a in assets:
        fname = a.get("originalFileName", "") or ""
        guessed_info = guess_info_from_filename(fname, compiled) if compiled else {}
        guessed = guessed_info.get("dt")
        guessed_descr = guessed_info.get("descr")

        # Merge time from current datetime if guessed has no time component
        if guessed:
            exif = a.get("exifInfo") or {}
            current_dt_str = None
            if isinstance(exif, dict):
                current_dt_str = exif.get("dateTimeOriginal")
            if not current_dt_str:
                current_dt_str = a.get("fileCreatedAt") or a.get("localDateTime") or ""
            current_dt = None
            if current_dt_str:
                try:
                    current_dt = datetime.fromisoformat(str(current_dt_str).replace('Z', '+00:00'))
                except Exception:
                    pass
            if current_dt:
                guessed = merge_time_from_source(guessed, current_dt)
        a["_guessed_iso"] = to_iso_z(guessed) if guessed else None
        a["_guessed_descr"] = guessed_descr or None
        exif = a.get("exifInfo") or {}
        if isinstance(exif, dict) and exif.get("dateTimeOriginal"):
            a["_source"] = "exif"
        elif a.get("fileCreatedAt") or a.get("localDateTime"):
            a["_source"] = "file"
        else:
            a["_source"] = "-"
        if not filter_flag:
            visible_assets.append(a)
        else:
            if a["_guessed_iso"]:
                visible_assets.append(a)

    count_visible = len(visible_assets)

    # No per-row EXIF load; use the `exifInfo` embedded in each returned
    # asset (if present) to populate description and current datetime.

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
        "<label><input type='checkbox' name='update_datetime' value='1'> Update datetime</label>"
        "<label><input type='checkbox' name='update_description' value='1'> Update description</label>"
        "<label><input type='checkbox' name='remove_tag_when_skipped' value='1'> Remove tag even when skipped</label>"
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
        rows.append(_asset_row(a, a.get("_guessed_iso"), a.get("_guessed_descr")))
    if not rows:
        rows.append("<tr><td colspan='7' class='muted'>No results.</td></tr>")

    table_html = (
        "<table><thead><tr>"
        "<th>ID</th><th>Filename</th><th>Current datetime</th><th>Guessed datetime</th><th>Source</th><th>Description</th><th>Guessed description</th>"
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


# per-row load EXIF endpoint removed: we now use `exifInfo` embedded in
# the search/metadata response where available and fall back to file
# timestamps otherwise.


# ---------- bulk update ----------
@app.post("/update-all")
def update_all() -> Response:
    """Perform bulk metadata updates for all visible assets.

    This POST endpoint repeats the search logic from `ui_search()` to make
    sure the same set of assets is operated on.  For each asset that yields
    a guessed datetime, the Immich API is called to set the
    `dateTimeOriginal`, `fileCreatedAt`, and `fileModifiedAt` fields to the
    guessed value (with time merged if necessary).  The search tag is
    removed from assets that are successfully updated.  The response page
    summarizes how many assets were updated, skipped, or errored.
    """
    tag_name = (request.form.get("tag") or "").strip()
    file_contains = (request.form.get("file") or "").strip()
    limit = int(request.form.get("limit") or "200")
    patterns_text = request.form.get("patterns") or ""
    # New: checkboxes control which metadata fields are updated
    update_datetime = (request.form.get("update_datetime") or "") == "1"
    update_description = (request.form.get("update_description") or "") == "1"
    remove_tag_when_skipped = (request.form.get("remove_tag_when_skipped") or "") == "1"
    compiled = compile_patterns(patterns_text) if patterns_text.strip() else []

    client = mk_client()
    tag_uuid = client.get_tag_id_by_name(tag_name) if tag_name else None

    updated = 0
    skipped = 0
    errors: List[str] = []

    assets: List[Dict[str, Any]] = []
    if tag_uuid:
        assets, _ = client.search_assets_by_tag(
            tag_id=tag_uuid, page=1, size=limit, filename_contains=file_contains or None
        )

    # If user unchecks all boxes, perform no changes
    if not update_datetime and not update_description and not remove_tag_when_skipped:
        back_qs = (
            "tag=" + quote(tag_name, safe="") +
            "&file=" + quote(file_contains, safe="") +
            "&limit=" + str(limit) +
            "&patterns=" + quote(patterns_text, safe="") +
            "&filter=1"
        )
        body = (
            "<h3>Bulk update finished</h3>"
            "<p>No update option selected. Nothing changed.</p>"
            "<p><a href='/ui/search?" + back_qs + "'>Back to search</a></p>"
        )
        return Response(_page(body, "Update all"), status=200)

    for a in assets:
        fname = a.get("originalFileName", "") or ""
        # Ensure we have detailed asset info (exif/description) before
        # attempting to guess/merge times.
        asset_id = a.get("id") or ""
        if asset_id:
            try:
                detailed = client.get_asset(asset_id)
                if isinstance(detailed, dict):
                    a.update(detailed)
            except Exception as ex:
                a["_detail_error"] = str(ex)
        # New: guess datetime and description together from filename
        guessed_info = guess_info_from_filename(fname, compiled) if compiled else {}
        guess_dt = guessed_info.get("dt")
        guessed_descr = guessed_info.get("descr")

        new_iso = None
        if update_datetime and guess_dt:
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

        # New: apply description update only when requested and available
        description_value = guessed_descr if (update_description and guessed_descr) else None

        # New: skip when selected updates cannot be derived for this asset
        if not new_iso and description_value is None:
            skipped += 1
            if remove_tag_when_skipped and tag_uuid:
                try:
                    asset_id = a.get("id", "")
                    client.remove_tag_from_asset(asset_id, tag_uuid)
                except Exception as tag_ex:
                    errors.append(f"{a.get('id','?')}: Tag removal failed on skipped asset: {tag_ex}")
            continue

        try:
            client.update_asset_metadata(
                a.get("id", ""),
                date_time_original_iso=new_iso,
                file_created_at_iso=new_iso,
                file_modified_at_iso=new_iso,
                description=description_value,
            )
            # Remove the tag used for search after successful update
            if tag_uuid:
                try:
                    asset_id = a.get("id", "")
                    client.remove_tag_from_asset(asset_id, tag_uuid)

                    # Verify removal to catch silent API failures.
                    refreshed = client.get_asset(asset_id)
                    tag_ids = set()
                    for t in (refreshed.get("tags") or []):
                        if isinstance(t, dict) and t.get("id"):
                            tag_ids.add(t.get("id"))

                    if tag_uuid in tag_ids:
                        errors.append(f"{asset_id}: Tag still present after remove call.")
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
