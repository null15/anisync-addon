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
    <title>AniSync</title>
    <style>
        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Inter, Arial, sans-serif;
            background:
                radial-gradient(circle at 15% 10%, rgba(0, 188, 220, .16), transparent 34%),
                radial-gradient(circle at 85% 85%, rgba(125, 79, 180, .14), transparent 36%),
                #111;
            color: #f3f3f3;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 22px;
        }}

        .modal {{
            width: min(900px, 100%);
            min-height: 560px;
            background:
                linear-gradient(135deg, rgba(255,255,255,.055), rgba(255,255,255,.025)),
                #202020;
            border: 1px solid rgba(255,255,255,.08);
            border-radius: 18px;
            box-shadow: 0 30px 90px rgba(0,0,0,.55);
            padding: 54px 58px;
        }}

        .title {{
            font-size: clamp(36px, 5vw, 54px);
            line-height: .96;
            font-weight: 900;
            letter-spacing: -.045em;
            margin: 0;
        }}

        .pill {{
            display: inline-block;
            margin-top: 14px;
            padding: 6px 12px;
            border-radius: 999px;
            background: rgba(255,255,255,.12);
            color: #ddd;
            font-size: 13px;
            font-weight: 700;
        }}

        .subtle {{
            margin-top: 22px;
            max-width: 620px;
            color: #9d9d9d;
            line-height: 1.55;
            font-size: 15px;
        }}

        .section {{
            margin-top: 34px;
        }}

        .section-title {{
            color: #8e8e8e;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: .14em;
            margin-bottom: 14px;
        }}

        .services {{
            display: flex;
            gap: 18px;
            flex-wrap: wrap;
        }}

        .service {{
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 12px 16px;
            border-radius: 12px;
            background: rgba(255,255,255,.055);
            color: #eee;
            font-weight: 700;
        }}

        input[type="checkbox"] {{
            width: 18px;
            height: 18px;
            accent-color: #00b7dc;
        }}

        .score-row {{
            display: flex;
            align-items: center;
            gap: 15px;
            flex-wrap: wrap;
        }}

        .stars {{
            display: flex;
            gap: 9px;
            align-items: center;
        }}

        .star {{
            appearance: none;
            border: 0;
            background: transparent;
            color: #4c4c4c;
            font-size: clamp(32px, 5vw, 52px);
            line-height: 1;
            padding: 0;
            cursor: pointer;
            transition: transform .12s ease, color .12s ease;
        }}

        .star.active {{
            color: #00b7dc;
        }}

        .star:hover {{
            transform: scale(1.08);
        }}

        .score-value {{
            min-width: 70px;
            text-align: center;
            color: #00b7dc;
            font-size: 28px;
            font-weight: 900;
        }}

        .score-value small {{
            color: #aaa;
            font-size: 16px;
            font-weight: 500;
        }}

        .status-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 10px;
        }}

        .status-chip {{
            border: 1px solid rgba(255,255,255,.10);
            background: rgba(255,255,255,.06);
            color: #eee;
            border-radius: 999px;
            padding: 13px 12px;
            font-size: 14px;
            font-weight: 800;
            cursor: pointer;
            transition: background .12s ease, transform .12s ease, border-color .12s ease;
        }}

        .status-chip:hover {{
            transform: translateY(-1px);
            background: rgba(255,255,255,.10);
        }}

        .status-chip.active {{
            background: #00a1c6;
            border-color: #00c4ea;
            color: white;
        }}

        .progress-wrap {{
            display: inline-flex;
            align-items: center;
            gap: 14px;
            padding: 10px;
            border-radius: 999px;
            background: rgba(255,255,255,.055);
        }}

        .round-btn {{
            width: 42px;
            height: 42px;
            border-radius: 50%;
            border: 0;
            background: rgba(255,255,255,.10);
            color: white;
            font-size: 24px;
            cursor: pointer;
        }}

        .progress-num {{
            width: 56px;
            border: 0;
            background: transparent;
            color: white;
            font-size: 24px;
            font-weight: 900;
            text-align: center;
            outline: none;
            appearance: textfield;
        }}

        .progress-num::-webkit-outer-spin-button,
        .progress-num::-webkit-inner-spin-button {{
            -webkit-appearance: none;
            margin: 0;
        }}

        .bottom {{
            display: flex;
            justify-content: space-between;
            gap: 18px;
            align-items: center;
            margin-top: 34px;
            flex-wrap: wrap;
        }}

        .send {{
            min-width: 250px;
            border: 0;
            border-radius: 10px;
            padding: 16px 26px;
            background: #008fad;
            color: white;
            font-size: 16px;
            font-weight: 900;
            cursor: pointer;
        }}

        .send:hover {{
            filter: brightness(1.08);
        }}

        .result {{
            color: #00e39f;
            font-weight: 800;
            display: none;
        }}

        details {{
            margin-top: 22px;
            color: #8d8d8d;
            font-size: 12px;
        }}

        summary {{
            cursor: pointer;
            width: fit-content;
        }}

        .ids {{
            margin-top: 8px;
            line-height: 1.5;
            word-break: break-all;
        }}

        @media (max-width: 760px) {{
            body {{
                align-items: flex-start;
                padding: 10px;
            }}

            .modal {{
                padding: 28px 22px;
                min-height: unset;
            }}

            .status-grid {{
                grid-template-columns: 1fr 1fr;
            }}

            .services {{
                gap: 10px;
            }}

            .send {{
                width: 100%;
            }}

            .bottom {{
                align-items: stretch;
            }}
        }}
    </style>
</head>
<body>
    <div class="modal">
        <h1 class="title">AniSync</h1>
        <span class="pill">MAL · AniList</span>

        <div class="subtle">
            Update list status, progress, and score without leaving the Stremio flow.
        </div>

        <form id="manageForm">
            <input type="hidden" name="meta_id" value="{safe_meta_id}">
            <input type="hidden" name="content_type" value="{safe_type}">
            <input type="hidden" name="status" id="statusInput" value="watching">
            <input type="hidden" name="score" id="scoreInput" value="">

            <div class="section">
                <div class="section-title">Send to services</div>
                <div class="services">
                    <label class="service">
                        <input type="checkbox" name="provider_mal" value="1" {mal_checked}>
                        MyAnimeList
                    </label>
                    <label class="service">
                        <input type="checkbox" name="provider_anilist" value="1" {anilist_checked}>
                        AniList
                    </label>
                </div>
            </div>

            <div class="section">
                <div class="section-title">Status</div>
                <div class="status-grid">
                    <button type="button" class="status-chip active" data-status="watching">Watching</button>
                    <button type="button" class="status-chip" data-status="plan_to_watch">Plan</button>
                    <button type="button" class="status-chip" data-status="completed">Completed</button>
                    <button type="button" class="status-chip" data-status="on_hold">On Hold</button>
                    <button type="button" class="status-chip" data-status="dropped">Dropped</button>
                </div>
            </div>

            <div class="section">
                <div class="section-title">Progress</div>
                <div class="progress-wrap">
                    <button type="button" class="round-btn" id="minusBtn">−</button>
                    <input class="progress-num" id="progressInput" type="number" name="progress" min="0" value="{default_progress}">
                    <button type="button" class="round-btn" id="plusBtn">+</button>
                </div>
            </div>

            <div class="section">
                <div class="section-title">Your score</div>
                <div class="score-row">
                    <div class="stars" id="stars">
                        <button type="button" class="star" data-score="1">★</button>
                        <button type="button" class="star" data-score="2">★</button>
                        <button type="button" class="star" data-score="3">★</button>
                        <button type="button" class="star" data-score="4">★</button>
                        <button type="button" class="star" data-score="5">★</button>
                        <button type="button" class="star" data-score="6">★</button>
                        <button type="button" class="star" data-score="7">★</button>
                        <button type="button" class="star" data-score="8">★</button>
                        <button type="button" class="star" data-score="9">★</button>
                        <button type="button" class="star" data-score="10">★</button>
                    </div>
                    <div class="score-value"><span id="scoreText">—</span> <small>/ 10</small></div>
                </div>
            </div>

            <div class="bottom">
                <div id="result" class="result"></div>
                <button type="submit" class="send">Update</button>
            </div>

            <details>
                <summary>details</summary>
                <div class="ids">
                    ID: {safe_meta_id}<br>
                    Type: {safe_type}<br>
                    MAL: {html.escape(str(ids.get("mal_id") or "not found"))}<br>
                    AniList: {html.escape(str(ids.get("anilist_id") or "not found"))}
                </div>
            </details>
        </form>
    </div>

    <script>
        const form = document.getElementById("manageForm");
        const result = document.getElementById("result");
        const statusInput = document.getElementById("statusInput");
        const scoreInput = document.getElementById("scoreInput");
        const scoreText = document.getElementById("scoreText");
        const progressInput = document.getElementById("progressInput");

        document.querySelectorAll(".status-chip").forEach((btn) => {{
            btn.addEventListener("click", () => {{
                document.querySelectorAll(".status-chip").forEach((b) => b.classList.remove("active"));
                btn.classList.add("active");
                statusInput.value = btn.dataset.status;
            }});
        }});

        function setScore(score) {{
            scoreInput.value = score;
            scoreText.textContent = score || "—";

            document.querySelectorAll(".star").forEach((star) => {{
                const value = Number(star.dataset.score);
                star.classList.toggle("active", score && value <= score);
            }});
        }}

        document.querySelectorAll(".star").forEach((star) => {{
            star.addEventListener("click", () => {{
                const score = Number(star.dataset.score);
                if (String(score) === scoreInput.value) {{
                    setScore("");
                }} else {{
                    setScore(score);
                }}
            }});
        }});

        document.getElementById("minusBtn").addEventListener("click", () => {{
            const current = Number(progressInput.value || 0);
            progressInput.value = Math.max(0, current - 1);
        }});

        document.getElementById("plusBtn").addEventListener("click", () => {{
            const current = Number(progressInput.value || 0);
            progressInput.value = current + 1;
        }});

        form.addEventListener("submit", async (e) => {{
            e.preventDefault();

            const fd = new FormData(form);

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