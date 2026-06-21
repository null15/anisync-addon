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
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Inter, Arial, sans-serif;
            background:
                radial-gradient(circle at top left, rgba(0, 170, 210, 0.18), transparent 35%),
                radial-gradient(circle at bottom right, rgba(140, 70, 180, 0.18), transparent 35%),
                #111;
            color: #f5f5f5;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}

        .modal {{
            width: min(920px, 100%);
            background: linear-gradient(145deg, #242424, #191919);
            border: 1px solid rgba(255,255,255,.08);
            border-radius: 22px;
            padding: 34px;
            box-shadow: 0 28px 90px rgba(0,0,0,.55);
        }}

        .header {{
            display: flex;
            justify-content: space-between;
            gap: 18px;
            align-items: flex-start;
            margin-bottom: 28px;
        }}

        h1 {{
            margin: 0;
            font-size: 36px;
            line-height: 1.05;
            font-weight: 800;
        }}

        .badge {{
            display: inline-block;
            margin-top: 12px;
            padding: 6px 12px;
            border-radius: 999px;
            background: rgba(255,255,255,.12);
            color: #ddd;
            font-size: 13px;
        }}

        .meta {{
            color: #aaa;
            line-height: 1.55;
            font-size: 14px;
            word-break: break-word;
            max-width: 380px;
        }}

        .section {{
            margin-top: 24px;
        }}

        .section-title {{
            color: #999;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: .12em;
            margin-bottom: 12px;
        }}

        .providers {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}

        label {{
            display: inline-flex;
            align-items: center;
            gap: 9px;
            padding: 12px 15px;
            border-radius: 12px;
            background: rgba(255,255,255,.07);
            border: 1px solid rgba(255,255,255,.08);
            font-weight: 600;
        }}

        input[type="checkbox"] {{
            width: 18px;
            height: 18px;
            accent-color: #00a9d6;
        }}

        .fields {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
        }}

        input[type="number"] {{
            width: 100%;
            padding: 15px 16px;
            border-radius: 14px;
            border: 1px solid rgba(255,255,255,.12);
            background: rgba(0,0,0,.24);
            color: white;
            font-size: 18px;
            outline: none;
        }}

        input[type="number"]:focus {{
            border-color: #00b8df;
            box-shadow: 0 0 0 3px rgba(0,184,223,.15);
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
        }}

        button {{
            border: 0;
            border-radius: 15px;
            padding: 16px 14px;
            color: white;
            background: linear-gradient(135deg, #00a6c8, #008aad);
            font-size: 15px;
            font-weight: 800;
            cursor: pointer;
            transition: transform .12s ease, filter .12s ease;
        }}

        button:hover {{
            transform: translateY(-1px);
            filter: brightness(1.08);
        }}

        button[value="completed"] {{
            background: linear-gradient(135deg, #00b894, #008f72);
        }}

        button[value="dropped"] {{
            background: linear-gradient(135deg, #d94f4f, #a93434);
        }}

        button[value="on_hold"] {{
            background: linear-gradient(135deg, #9b59b6, #76448a);
        }}

        button[value="plan_to_watch"] {{
            background: linear-gradient(135deg, #3498db, #2471a3);
        }}

        .result {{
            margin-top: 22px;
            padding: 14px 16px;
            border-radius: 14px;
            background: rgba(0,0,0,.22);
            color: #00e39f;
            display: none;
            font-weight: 700;
        }}

        @media (max-width: 720px) {{
            body {{
                align-items: flex-start;
                padding: 12px;
            }}

            .modal {{
                padding: 22px;
                border-radius: 18px;
            }}

            .header {{
                flex-direction: column;
            }}

            h1 {{
                font-size: 30px;
            }}

            .fields {{
                grid-template-columns: 1fr;
            }}

            .grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="modal">
        <div class="header">
            <div>
                <h1>⚙️ AniSync Manage</h1>
                <span class="badge">Anime status editor</span>
            </div>
            <div class="meta">
                ID: {safe_meta_id}<br>
                Type: {safe_type}<br>
                MAL: {html.escape(str(ids.get("mal_id") or "not found"))}<br>
                AniList: {html.escape(str(ids.get("anilist_id") or "not found"))}
            </div>
        </div>

        <form id="manageForm">
            <input type="hidden" name="meta_id" value="{safe_meta_id}">
            <input type="hidden" name="content_type" value="{safe_type}">

            <div class="section">
                <div class="section-title">Send to services</div>
                <div class="providers">
                    <label><input type="checkbox" name="provider_mal" value="1" {mal_checked}> MyAnimeList</label>
                    <label><input type="checkbox" name="provider_anilist" value="1" {anilist_checked}> AniList</label>
                </div>
            </div>

            <div class="section fields">
                <div>
                    <div class="section-title">Progress</div>
                    <input type="number" name="progress" min="0" value="{default_progress}">
                </div>
                <div>
                    <div class="section-title">Score, optional, 0–10</div>
                    <input type="number" name="score" min="0" max="10" step="1" placeholder="Keep current score">
                </div>
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
                result.style.color = "#00e39f";
                result.textContent = data.message || "Updated.";
            }} else {{
                result.style.color = "#ff7675";
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