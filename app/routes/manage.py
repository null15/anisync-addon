import html
import logging
import urllib.parse

from quart import Blueprint, request

from app.lib.id_resolver import resolve, resolve_anilist_to_kitsu, resolve_mal_to_kitsu, resolve_simkl_to_kitsu
from app.routes.utils import is_valid_user_id, rate_limit, respond_with
from app.services.db import get_user
from config import Config

manage_bp = Blueprint("manage", __name__)


STATUS_MAP = {
    "watching": {
        "label": "Watching",
        "mal": "watching",
        "anilist": "CURRENT",
    },
    "plan_to_watch": {
        "label": "Plan to Watch",
        "mal": "plan_to_watch",
        "anilist": "PLANNING",
    },
    "completed": {
        "label": "Completed",
        "mal": "completed",
        "anilist": "COMPLETED",
    },
    "on_hold": {
        "label": "On Hold",
        "mal": "on_hold",
        "anilist": "PAUSED",
    },
    "dropped": {
        "label": "Dropped",
        "mal": "dropped",
        "anilist": "DROPPED",
    },
}


def _base_url() -> str:
    return f"{Config.PROTOCOL}://{Config.REDIRECT_URL}"


def _parse_content_id(raw_id: str) -> tuple[str, int | None]:
    """
    Accepts:
      kitsu:49912
      kitsu:49912:12
      mal:61687
      anilist:192808
    Returns:
      cleaned_meta_id, episode
    """
    raw_id = urllib.parse.unquote(raw_id or "").strip()
    raw_id = raw_id.split("/")[0]

    episode = None
    parts = raw_id.split(":")
    if len(parts) >= 3 and parts[-1].isdigit():
        episode = int(parts[-1])
        raw_id = ":".join(parts[:-1])

    return raw_id, episode


async def _resolve_ids(meta_id: str) -> dict:
    kitsu_id = None
    mal_id = None
    anilist_id = None
    simkl_id = None

    if meta_id.startswith(("kitsu:", "kitsu-", "kitsu_")):
        kitsu_id = meta_id[6:]
    elif meta_id.startswith(("mal:", "mal-", "mal_")):
        mal_id = meta_id[4:]
        kitsu_id = await resolve_mal_to_kitsu(mal_id)
    elif meta_id.startswith(("anilist:", "anilist-", "anilist_")):
        anilist_id = meta_id[8:]
        kitsu_id = await resolve_anilist_to_kitsu(anilist_id)
    elif meta_id.startswith(("simkl:", "simkl-", "simkl_")):
        simkl_id = meta_id[6:]
        kitsu_id = await resolve_simkl_to_kitsu(simkl_id)

    if kitsu_id:
        resolved_mal, resolved_anilist = await resolve(kitsu_id)
        mal_id = mal_id or resolved_mal
        anilist_id = anilist_id or resolved_anilist

    return {
        "kitsu_id": kitsu_id,
        "mal_id": mal_id,
        "anilist_id": anilist_id,
        "simkl_id": simkl_id,
    }


@manage_bp.route("/<user_id>/stream/<string:content_type>/<path:content_id>.json")
@rate_limit(limit=120, period_seconds=60)
async def handle_stream(user_id: str, content_type: str, content_id: str):
    if not is_valid_user_id(user_id):
        return await respond_with({"streams": []})

    user = get_user(user_id)
    if not user:
        return await respond_with({"streams": []})

    decoded_id = urllib.parse.unquote(content_id)
    manage_url = (
        f"{_base_url()}/{user_id}/manage"
        f"?id={urllib.parse.quote(decoded_id, safe='')}"
        f"&type={urllib.parse.quote(content_type, safe='')}"
    )

    return await respond_with(
        {
            "streams": [
                {
                    "name": "⚙️ AniSync",
                    "title": " ",
                    "externalUrl": manage_url,
                    "behaviorHints": {
                        "notWebReady": True,
                    },
                }
            ]
        }
    )


@manage_bp.route("/<user_id>/manage", methods=["GET"])
@rate_limit(limit=60, period_seconds=60)
async def manage_page(user_id: str):
    if not is_valid_user_id(user_id):
        return "Invalid user.", 400

    user = get_user(user_id)
    if not user:
        return "Unknown user.", 404

    raw_id = request.args.get("id", "")
    content_type = request.args.get("type", "series")
    meta_id, episode = _parse_content_id(raw_id)
    ids = await _resolve_ids(meta_id)

    safe_meta_id = html.escape(meta_id)
    safe_type = html.escape(content_type)
    default_progress = episode or 0

    mal_checked = "checked" if user.get("mal_access_token") and user.get("mal_enabled", True) and ids.get("mal_id") else ""
    anilist_checked = (
        "checked" if user.get("anilist_token") and user.get("anilist_enabled", True) and ids.get("anilist_id") else ""
    )

    status_buttons = ""
    for key, data in STATUS_MAP.items():
        status_buttons += f"""
        <button type="submit" name="status" value="{key}" class="status-btn">
            {html.escape(data["label"])}
        </button>
        """

    return f"""
<!doctype html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AniSync Manage</title>
    <style>
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: #121212;
            color: #f5f5f5;
        }}
        .wrap {{
            max-width: 720px;
            margin: 0 auto;
            padding: 24px;
        }}
        .card {{
            background: #1f1f1f;
            border-radius: 16px;
            padding: 22px;
            box-shadow: 0 10px 40px rgba(0,0,0,.35);
        }}
        h1 {{
            margin: 0 0 8px;
            font-size: 28px;
        }}
        .muted {{
            color: #aaa;
            font-size: 14px;
            margin-bottom: 18px;
            word-break: break-all;
        }}
        .providers, .row {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin: 14px 0;
        }}
        label {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: #2b2b2b;
            padding: 10px 12px;
            border-radius: 10px;
        }}
        input[type="number"] {{
            width: 100%;
            box-sizing: border-box;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid #444;
            background: #111;
            color: white;
            font-size: 16px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin-top: 16px;
        }}
        button {{
            border: 0;
            border-radius: 12px;
            padding: 14px;
            color: white;
            background: #0099b8;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
        }}
        .status-btn:nth-child(4) {{
            background: #c0392b;
        }}
        .status-btn:nth-child(5) {{
            background: #8e44ad;
        }}
        .section {{
            margin-top: 22px;
        }}
        .section-title {{
            color: #bbb;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: .08em;
            margin-bottom: 8px;
        }}
        .result {{
            margin-top: 16px;
            color: #0fd18a;
            display: none;
        }}
        @media (max-width: 520px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
            .wrap {{
                padding: 14px;
            }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="card">
            <h1>⚙️ AniSync Manage</h1>
            <div class="muted">
                ID: {safe_meta_id}<br>
                Type: {safe_type}<br>
                MAL: {html.escape(str(ids.get("mal_id") or "not found"))} · AniList: {html.escape(str(ids.get("anilist_id") or "not found"))}
            </div>

            <form id="manageForm">
                <input type="hidden" name="meta_id" value="{safe_meta_id}">
                <input type="hidden" name="content_type" value="{safe_type}">

                <div class="section">
                    <div class="section-title">Send to</div>
                    <div class="providers">
                        <label><input type="checkbox" name="provider_mal" value="1" {mal_checked}> MAL</label>
                        <label><input type="checkbox" name="provider_anilist" value="1" {anilist_checked}> AniList</label>
                    </div>
                </div>

                <div class="section">
                    <div class="section-title">Progress</div>
                    <input type="number" name="progress" min="0" value="{default_progress}">
                </div>

                <div class="section">
                    <div class="section-title">Score, optional, 0–10</div>
                    <input type="number" name="score" min="0" max="10" step="1" placeholder="Leave empty to keep current score">
                </div>

                <div class="section">
                    <div class="section-title">Set status</div>
                    <div class="grid">
                        {status_buttons}
                    </div>
                </div>

                <div id="result" class="result"></div>
            </form>
        </div>
    </div>

    <script>
        const form = document.getElementById("manageForm");
        const result = document.getElementById("result");

        form.addEventListener("submit", async (e) => {{
            e.preventDefault();

            const clicked = e.submitter;
            const fd = new FormData(form);

            if (clicked && clicked.name) {{
                fd.set(clicked.name, clicked.value);
            }}

            result.style.display = "block";
            result.style.color = "#aaa";
            result.textContent = "Updating...";

            const res = await fetch("/{user_id}/manage/update", {{
                method: "POST",
                body: fd,
            }});

            const data = await res.json();

            if (res.ok) {{
                result.style.color = "#0fd18a";
                result.textContent = data.message || "Updated.";
            }} else {{
                result.style.color = "#ff6961";
                result.textContent = data.message || "Update failed.";
            }}
        }});
    </script>
</body>
</html>
"""


@manage_bp.route("/<user_id>/manage/update", methods=["POST"])
@rate_limit(limit=30, period_seconds=60)
async def manage_update(user_id: str):
    if not is_valid_user_id(user_id):
        return {"status": "error", "message": "Invalid user."}, 400

    user = get_user(user_id)
    if not user:
        return {"status": "error", "message": "Unknown user."}, 404

    form = await request.form

    meta_id = form.get("meta_id", "")
    status_key = form.get("status", "")
    progress_raw = form.get("progress", "").strip()
    score_raw = form.get("score", "").strip()

    if status_key not in STATUS_MAP:
        return {"status": "error", "message": "Invalid status."}, 400

    progress = None
    if progress_raw:
        try:
            progress = max(0, int(progress_raw))
        except ValueError:
            return {"status": "error", "message": "Invalid progress."}, 400

    score = None
    if score_raw:
        try:
            score = max(0, min(10, int(score_raw)))
        except ValueError:
            return {"status": "error", "message": "Invalid score."}, 400

    ids = await _resolve_ids(meta_id)
    results = []

    if form.get("provider_mal") and user.get("mal_access_token") and ids.get("mal_id"):
        try:
            from app.api import mal as mal_api

            await mal_api.set_list_status(
                token=user["mal_access_token"],
                anime_id=str(ids["mal_id"]),
                status=STATUS_MAP[status_key]["mal"],
                progress=progress,
                score=score,
            )
            results.append("MAL OK")
        except Exception as e:
            logging.error("MAL manual update failed: %s", e)
            results.append("MAL failed")

    if form.get("provider_anilist") and user.get("anilist_token") and ids.get("anilist_id"):
        try:
            from app.api import anilist as al_api

            await al_api.save_manual_entry(
                token=user["anilist_token"],
                anilist_id=int(ids["anilist_id"]),
                status=STATUS_MAP[status_key]["anilist"],
                progress=progress,
                score=score,
            )
            results.append("AniList OK")
        except Exception as e:
            logging.error("AniList manual update failed: %s", e)
            results.append("AniList failed")

    if not results:
        return {
            "status": "error",
            "message": "Nothing updated. Check that MAL/AniList is connected and selected.",
        }, 400

    return {
        "status": "success",
        "message": " · ".join(results),
    }