import html
import logging
from turtle import width
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

    safe_meta_id = html.escape(meta_id, quote=True)
    safe_type = html.escape(content_type, quote=True)
    safe_user_path = urllib.parse.quote(user_id, safe="")
    default_progress = episode or 0

    mal_available = bool(user.get("mal_access_token") and ids.get("mal_id"))
    anilist_available = bool(user.get("anilist_token") and ids.get("anilist_id"))

    mal_checked = "checked" if mal_available and user.get("mal_enabled", True) else ""
    anilist_checked = "checked" if anilist_available and user.get("anilist_enabled", True) else ""

    mal_disabled = "" if mal_available else "disabled"
    anilist_disabled = "" if anilist_available else "disabled"

    mal_card_class = "provider-card" + ("" if mal_available else " is-disabled")
    anilist_card_class = "provider-card" + ("" if anilist_available else " is-disabled")

    mal_id_text = html.escape(str(ids.get("mal_id") or "not found"), quote=True)
    anilist_id_text = html.escape(str(ids.get("anilist_id") or "not found"), quote=True)

    mal_caption = f"MAL ID {mal_id_text}" if ids.get("mal_id") else "MAL ID not found"
    anilist_caption = f"AniList ID {anilist_id_text}" if ids.get("anilist_id") else "AniList ID not found"

    mal_state = "Selected" if mal_checked else ("Unavailable" if not mal_available else "Off")
    anilist_state = "Selected" if anilist_checked else ("Unavailable" if not anilist_available else "Off")

    status_options = ""
    for key, data in STATUS_MAP.items():
        selected = "selected" if key == "watching" else ""
        status_options += f"""
            <option value="{html.escape(key, quote=True)}" {selected}>
                {html.escape(data["label"])}
            </option>
        """

    star_buttons = "".join(
        f"""
        <button type="button" class="star-btn" data-score="{score}" aria-label="Set score {score} out of 10">
            ★
        </button>
        """
        for score in range(1, 11)
    )

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AniSync Manage</title>
    <style>
        :root {{
            --bg: #181818;
            --panel: #202020;
            --panel-soft: #242424;
            --line: #333;
            --line-soft: #2b2b2b;
            --text: #f4f4f5;
            --muted: #87909d;
            --muted-2: #a4aab4;
            --blue: #2f7df6;
            --green: #0aa66a;
            --red: #ff4d5a;
            --gold: #f4c542;
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family: Arial, Helvetica, sans-serif;
            background: var(--bg);
            color: var(--text);
        }}

        .topbar {{
            height: 48px;
            display: flex;
            align-items: center;
            padding: 0 22px;
            border-bottom: 1px solid #262626;
            background: #1f1f1f;
            font-weight: 800;
            letter-spacing: .01em;
        }}

        .wrap {{
            width: min(780px, calc(100% - 28px));
            margin: 0 auto;
            padding: 28px 0 46px;
        }}

        .sync-card {{
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: 0 16px 45px rgba(0, 0, 0, .22);
            overflow: hidden;
        }}

        .card-head {{
            padding: 18px 20px;
            border-bottom: 1px solid var(--line-soft);
        }}

        .title-block strong {{
            display: block;
            font-size: 16px;
        }}

        .title-block span {{
            display: block;
            color: var(--muted);
            font-size: 12px;
            margin-top: 4px;
            word-break: break-word;
        }}

        form {{
            padding: 28px 20px 20px;
        }}

        .section {{
            margin-top: 22px;
        }}

        .section:first-child {{
            margin-top: 0;
        }}

        .section-title {{
            color: #c9cdd5;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: .12em;
            margin-bottom: 10px;
            font-weight: 800;
        }}

        .providers {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }}

        .provider-card {{
            min-height: 72px;
            border: 1px solid var(--line);
            background: #1d1d1d;
            border-radius: 12px;
            padding: 13px;
            display: grid;
            grid-template-columns: auto 1fr auto;
            align-items: center;
            gap: 12px;
            cursor: pointer;
            transition: border-color .18s ease, background .18s ease, transform .18s ease;
        }}

        .provider-card:hover {{
            border-color: #4a4a4a;
            transform: translateY(-1px);
        }}

        .provider-card.is-disabled {{
            opacity: .52;
            cursor: not-allowed;
        }}

        .provider-card input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .provider-face {{
            width: 34px;
            height: 34px;
            border-radius: 9px;
            display: grid;
            place-items: center;
            font-size: 11px;
            font-weight: 900;
            color: white;
            background: #3552a5;
            position: relative;
        }}

        .provider-face.anilist {{
            background: #1d9bd7;
        }}

        .provider-check {{
            position: absolute;
            right: -5px;
            bottom: -5px;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            display: grid;
            place-items: center;
            font-size: 10px;
            background: #343434;
            border: 1px solid var(--line);
            color: transparent;
        }}

        .provider-card input:checked + .provider-face .provider-check {{
            color: white;
            background: var(--green);
            border-color: var(--green);
        }}

        .provider-copy strong {{
            display: block;
            font-size: 14px;
            margin-bottom: 4px;
        }}

        .provider-copy small {{
            color: var(--muted);
            font-size: 12px;
        }}

        .provider-state {{
            color: var(--muted);
            font-size: 12px;
            font-weight: 800;
        }}

        .provider-card input:checked ~ .provider-state {{
            color: var(--green);
        }}

        .action-grid {{
            display: grid;
            grid-template-columns: 240px 152px;
            gap: 14px;
            align-items: end;
        }}

        .status-field {{
            width: min(240px, 100%);
        }}

        .progress-field {{
            width: 152px;
        }}

        .control {{
            width: 100%;
            height: 46px;
            border: 1px solid #3b3b3b;
            border-radius: 10px;
            background: #151515;
            color: white;
            padding: 0 12px;
            font-size: 15px;
            outline: none;
        }}

        .control:focus {{
            border-color: var(--blue);
        }}

        select.control {{
            appearance: auto;
        }}

        .progress-wrap {{
            display: grid;
            grid-template-columns: 42px 54px 42px;
            gap: 7px;
        }}

        .progress-input {{
            padding: 0 4px;
            text-align: center;
            font-weight: 800;
            appearance: textfield;
            -moz-appearance: textfield;
        }}

        .progress-input::-webkit-outer-spin-button,
        .progress-input::-webkit-inner-spin-button {{
            -webkit-appearance: none;
            margin: 0;
        }}

        .step-btn {{
            border: 1px solid var(--line);
            background: var(--panel-soft);
            color: white;
            border-radius: 10px;
            font-size: 20px;
            cursor: pointer;
            transition: background .16s ease, transform .16s ease;
        }}

        .step-btn:hover {{
            background: #2d2d2d;
            transform: translateY(-1px);
        }}

        .score-panel {{
            width: fit-content;
            max-width: 100%;
            border: 1px solid var(--line);
            background: #1d1d1d;
            border-radius: 12px;
            padding: 14px;
        }}

        .stars {{
            display: flex;
            gap: 4px;
            flex-wrap: wrap;
        }}

        .star-btn {{
            width: 28px;
            height: 30px;
            border: 0;
            background: transparent;
            color: #4d4d4d;
            font-size: 23px;
            line-height: 1;
            padding: 0;
            cursor: pointer;
            transition: color .16s ease, transform .16s ease, text-shadow .16s ease;
        }}

        .star-btn:hover,
        .star-btn.is-active {{
            color: var(--gold);
            transform: translateY(-1px) scale(1.08);
        }}

        .star-btn.is-active {{
            text-shadow: 0 0 12px rgba(244, 197, 66, .22);
        }}

        .star-btn.is-pop {{
            animation: star-pop .28s ease;
        }}

        @keyframes star-pop {{
            0% {{ transform: scale(1); }}
            45% {{ transform: scale(1.32) rotate(-7deg); }}
            100% {{ transform: translateY(-1px) scale(1.08); }}
        }}

        .score-meta {{
            margin-top: 10px;
            display: flex;
            align-items: baseline;
            gap: 10px;
            min-height: 22px;
        }}

        .score-meta strong {{
            font-size: 14px;
        }}

        .score-meta span {{
            color: var(--muted);
            font-size: 13px;
        }}

        .submit-row {{
            margin-top: 24px;
        }}

        .submit-btn {{
            width: 100%;
            height: 48px;
            border: 0;
            border-radius: 10px;
            background: var(--blue);
            color: white;
            font-weight: 900;
            font-size: 15px;
            cursor: pointer;
            transition: background .16s ease, transform .16s ease, opacity .16s ease;
        }}

        .submit-btn:hover {{
            background: #3b86ff;
            transform: translateY(-1px);
        }}

        .submit-btn:disabled {{
            opacity: .7;
            cursor: wait;
            transform: none;
        }}

        .submit-btn.is-loading .spin {{
            display: inline-block;
            animation: spin .8s linear infinite;
        }}

        @keyframes spin {{
            to {{ transform: rotate(360deg); }}
        }}

        .result {{
            border-top: 1px solid var(--line-soft);
            padding: 20px;
            background: #1b1b1b;
        }}

        .result[hidden] {{
            display: none;
        }}

        .result-title {{
            font-weight: 900;
            margin-bottom: 12px;
        }}

        .result-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }}

        .result-line {{
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 11px 12px;
            display: grid;
            grid-template-columns: auto 1fr auto;
            gap: 8px;
            align-items: center;
            color: var(--muted-2);
            font-size: 13px;
        }}

        .result-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #777;
        }}

        .result-line strong {{
            font-size: 12px;
            color: var(--muted);
        }}

        .result-line.is-pending .result-dot {{
            background: #18aef5;
            animation: pulse .8s ease-in-out infinite alternate;
        }}

        .result-line.is-ok {{
            border-color: rgba(10, 166, 106, .45);
        }}

        .result-line.is-ok .result-dot {{
            background: var(--green);
        }}

        .result-line.is-ok strong {{
            color: var(--green);
        }}

        .result-line.is-failed {{
            border-color: rgba(255, 77, 90, .45);
        }}

        .result-line.is-failed .result-dot {{
            background: var(--red);
        }}

        .result-line.is-failed strong {{
            color: var(--red);
        }}

        @keyframes pulse {{
            from {{ opacity: .35; }}
            to {{ opacity: 1; }}
        }}

        .details {{
            border-top: 1px solid var(--line-soft);
            padding: 16px 20px;
            color: var(--muted);
            font-size: 12px;
            line-height: 1.65;
        }}

        .details summary {{
            cursor: pointer;
            color: #b8bec9;
            font-weight: 800;
        }}

        .details code {{
            color: #cbd0d8;
            word-break: break-word;
        }}

        @media (max-width: 640px) {{
            .score-panel {{
                width: 100%;
            }}
            
            .wrap {{
                width: min(100% - 24px, 780px);
                padding-top: 22px;
            }}

            .card-head,
            form,
            .result,
            .details {{
                padding-left: 16px;
                padding-right: 16px;
            }}

            .providers,
            .action-grid,
            .result-grid {{
                grid-template-columns: 1fr;
            }}

            .status-field,
            .progress-field {{
                width: 100%;
            }}

            .progress-wrap {{
                grid-template-columns: 42px 1fr 42px;
            }}

            .star-btn {{
                width: 26px;
            }}
        }}
    </style>
</head>
<body>
    <div class="topbar">AniSync</div>

    <main class="wrap">
        <section class="sync-card">
            <div class="card-head">
                <div class="title-block">
                    <strong>Tracker</strong>
                    <span>{safe_meta_id}</span>
                </div>
            </div>

            <form id="manageForm">
                <input type="hidden" name="meta_id" value="{safe_meta_id}">
                <input type="hidden" name="content_type" value="{safe_type}">
                <input type="hidden" name="score" id="scoreInput" value="">

                <div class="section">
                    <div class="section-title">Send update to</div>

                    <div class="providers">
                        <label class="{mal_card_class}">
                            <input type="checkbox" name="provider_mal" value="1" {mal_checked} {mal_disabled}>
                            <span class="provider-face">
                                MAL
                                <span class="provider-check">✓</span>
                            </span>
                            <span class="provider-copy">
                                <strong>MyAnimeList</strong>
                                <small>{mal_caption}</small>
                            </span>
                            <span class="provider-state">{mal_state}</span>
                        </label>

                        <label class="{anilist_card_class}">
                            <input type="checkbox" name="provider_anilist" value="1" {anilist_checked} {anilist_disabled}>
                            <span class="provider-face anilist">
                                AL
                                <span class="provider-check">✓</span>
                            </span>
                            <span class="provider-copy">
                                <strong>AniList</strong>
                                <small>{anilist_caption}</small>
                            </span>
                            <span class="provider-state">{anilist_state}</span>
                        </label>
                    </div>
                </div>

                <div class="section action-grid">
                    <div class="status-field">
                        <div class="section-title">Status</div>
                        <select class="control" name="status" required>
                            {status_options}
                        </select>
                    </div>

                    <div class="progress-field">
                        <div class="section-title">Progress</div>
                        <div class="progress-wrap">
                            <button type="button" class="step-btn" data-step="-1" aria-label="Decrease progress">−</button>
                            <input class="control progress-input" id="progressInput" type="number" name="progress" min="0" value="{default_progress}">
                            <button type="button" class="step-btn" data-step="1" aria-label="Increase progress">+</button>
                        </div>
                    </div>
                </div>

                <div class="section">
                    <div class="section-title">Score</div>

                    <div class="score-panel">
                        <div class="stars" id="stars">
                            {star_buttons}
                        </div>

                        <div class="score-meta">
                            <strong id="scoreValue">— / 10</strong>
                            <span id="scoreLabel">Leave empty to keep current score</span>
                        </div>
                    </div>
                </div>

                <div class="submit-row">
                    <button class="submit-btn" id="submitBtn" type="submit">
                        <span class="spin">↻</span> Update tracking
                    </button>
                </div>
            </form>

            <div id="result" class="result" hidden>
                <div class="result-title" id="resultTitle">Updating...</div>

                <div class="result-grid">
                    <div class="result-line is-skipped" data-result-provider="mal">
                        <span class="result-dot"></span>
                        <span>MyAnimeList</span>
                        <strong>Waiting</strong>
                    </div>

                    <div class="result-line is-skipped" data-result-provider="anilist">
                        <span class="result-dot"></span>
                        <span>AniList</span>
                        <strong>Waiting</strong>
                    </div>
                </div>
            </div>

            <details class="details">
                <summary>Details</summary>
                <div>ID: <code>{safe_meta_id}</code></div>
                <div>Type: <code>{safe_type}</code></div>
                <div>MAL: <code>{mal_id_text}</code></div>
                <div>AniList: <code>{anilist_id_text}</code></div>
            </details>
        </section>
    </main>

    <script>
        const form = document.getElementById("manageForm");
        const result = document.getElementById("result");
        const resultTitle = document.getElementById("resultTitle");
        const submitBtn = document.getElementById("submitBtn");

        const progressInput = document.getElementById("progressInput");
        const scoreInput = document.getElementById("scoreInput");
        const scoreValue = document.getElementById("scoreValue");
        const scoreLabel = document.getElementById("scoreLabel");
        const starsWrap = document.getElementById("stars");
        const stars = document.querySelectorAll(".star-btn");

        const scoreLabels = {{
            "10": "Masterpiece",
            "9": "Great",
            "8": "Very Good",
            "7": "Good",
            "6": "Fine",
            "5": "Average",
            "4": "Bad",
            "3": "Very Bad",
            "2": "Horrible",
            "1": "Appalling"
        }};

        document.querySelectorAll(".provider-card input[type='checkbox']").forEach((input) => {{
            input.addEventListener("change", () => {{
                const state = input.closest(".provider-card").querySelector(".provider-state");
                state.textContent = input.checked ? "Selected" : "Off";
            }});
        }});

        document.querySelectorAll("[data-step]").forEach((button) => {{
            button.addEventListener("click", () => {{
                const step = Number(button.dataset.step);
                const current = Number(progressInput.value || 0);
                progressInput.value = Math.max(0, current + step);
            }});
        }});

        function paintStars(value, animateButton) {{
            const numericValue = Number(value || 0);

            stars.forEach((star) => {{
                const starScore = Number(star.dataset.score);
                star.classList.toggle("is-active", starScore <= numericValue);

                if (animateButton && star === animateButton) {{
                    star.classList.remove("is-pop");
                    void star.offsetWidth;
                    star.classList.add("is-pop");
                }}
            }});
        }}

        function showScore(value) {{
            if (value) {{
                scoreValue.textContent = value + " / 10";
                scoreLabel.textContent = "(" + value + ") " + scoreLabels[value];
            }} else {{
                scoreValue.textContent = "— / 10";
                scoreLabel.textContent = "Leave empty to keep current score";
            }}
        }}

        function syncScoreUi(value, animateButton) {{
            paintStars(value, animateButton);
            showScore(value);
        }}

        stars.forEach((star) => {{
            star.addEventListener("mouseenter", () => {{
                syncScoreUi(star.dataset.score, null);
            }});

            star.addEventListener("click", () => {{
                scoreInput.value = star.dataset.score;
                syncScoreUi(scoreInput.value, star);
            }});
        }});

        starsWrap.addEventListener("mouseleave", () => {{
            syncScoreUi(scoreInput.value, null);
        }});

        function setProviderResult(provider, state) {{
            const line = document.querySelector('[data-result-provider="' + provider + '"]');
            if (!line) return;

            const text = line.querySelector("strong");
            line.classList.remove("is-pending", "is-ok", "is-failed", "is-skipped");

            if (state === "pending") {{
                line.classList.add("is-pending");
                text.textContent = "Updating";
            }} else if (state === "ok") {{
                line.classList.add("is-ok");
                text.textContent = "OK";
            }} else if (state === "failed") {{
                line.classList.add("is-failed");
                text.textContent = "Failed";
            }} else {{
                line.classList.add("is-skipped");
                text.textContent = "Skipped";
            }}
        }}

        function providerFromMessage(message, providerName) {{
            const lower = (message || "").toLowerCase();
            const name = providerName.toLowerCase();

            if (lower.includes(name + " ok")) return "ok";
            if (lower.includes(name + " failed")) return "failed";

            return "skipped";
        }}

        function normalizeProviders(data) {{
            const providers = data.providers || {{}};
            const message = data.message || "";

            return {{
                mal: providers.mal || providerFromMessage(message, "MAL"),
                anilist: providers.anilist || providerFromMessage(message, "AniList")
            }};
        }}

        form.addEventListener("submit", async (e) => {{
            e.preventDefault();

            const fd = new FormData(form);

            result.hidden = false;
            resultTitle.textContent = "Updating...";

            setProviderResult("mal", fd.get("provider_mal") ? "pending" : "skipped");
            setProviderResult("anilist", fd.get("provider_anilist") ? "pending" : "skipped");

            submitBtn.disabled = true;
            submitBtn.classList.add("is-loading");

            try {{
                const res = await fetch("/{safe_user_path}/manage/update", {{
                    method: "POST",
                    body: fd,
                }});

                const data = await res.json().catch(() => ({{ message: "Update failed." }}));
                const providers = normalizeProviders(data);

                setProviderResult("mal", providers.mal);
                setProviderResult("anilist", providers.anilist);

                const states = Object.values(providers);
                const okCount = states.filter((state) => state === "ok").length;
                const failedCount = states.filter((state) => state === "failed").length;

                if (res.ok && failedCount === 0 && okCount > 0) {{
                    resultTitle.textContent = "Updated";
                }} else if (res.ok && okCount > 0 && failedCount > 0) {{
                    resultTitle.textContent = "Partially updated";
                }} else {{
                    resultTitle.textContent = "Update failed";
                }}
            }} catch (err) {{
                setProviderResult("mal", "failed");
                setProviderResult("anilist", "failed");
                resultTitle.textContent = "Update failed";
            }} finally {{
                submitBtn.disabled = false;
                submitBtn.classList.remove("is-loading");
            }}
        }});

        syncScoreUi("", null);
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