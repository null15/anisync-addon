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

    safe_user_id = html.escape(user_id)
    mal_available = bool(user.get("mal_access_token") and user.get("mal_enabled", True) and ids.get("mal_id"))
    anilist_available = bool(user.get("anilist_token") and user.get("anilist_enabled", True) and ids.get("anilist_id"))

    mal_checked = "checked" if mal_available else ""
    anilist_checked = "checked" if anilist_available else ""
    mal_disabled = "" if mal_available else "disabled"
    anilist_disabled = "" if anilist_available else "disabled"

    status_details = {
        "watching": ("Currently active", "▶"),
        "plan_to_watch": ("Queue it for later", "+"),
        "completed": ("Finished", "✓"),
        "on_hold": ("Paused", "Ⅱ"),
        "dropped": ("Stopped", "×"),
    }

    status_buttons = ""
    for key, data in STATUS_MAP.items():
        detail, mark = status_details.get(key, ("Update status", "•"))
        checked = "checked" if key == "watching" else ""
        status_buttons += f"""
                    <label class="status-card">
                        <input type="radio" name="status" value="{html.escape(key)}" {checked} required>
                        <span class="status-mark">{html.escape(mark)}</span>
                        <span class="status-copy">
                            <strong>{html.escape(data["label"])}</strong>
                            <small>{html.escape(detail)}</small>
                        </span>
                    </label>
        """

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AniSync</title>
    <style>
        :root {{
            color-scheme: dark;
            --bg: #101114;
            --panel: #181a20;
            --panel-2: #1f222b;
            --line: rgba(255,255,255,.105);
            --line-strong: rgba(255,255,255,.18);
            --text: #f3f6fb;
            --muted: #8f98aa;
            --muted-2: #687184;
            --cyan: #42d8ff;
            --blue: #6d91ff;
            --pink: #ff5fa2;
            --green: #39d98a;
            --red: #ff6b7a;
            --shadow: 0 24px 70px rgba(0,0,0,.42);
            --radius: 24px;
        }}

        * {{
            box-sizing: border-box;
        }}

        html {{
            min-height: 100%;
            background:
                radial-gradient(circle at 12% 0%, rgba(66,216,255,.13), transparent 34rem),
                radial-gradient(circle at 92% 18%, rgba(255,95,162,.11), transparent 30rem),
                linear-gradient(180deg, #111318 0%, #0d0e12 100%);
        }}

        body {{
            min-height: 100vh;
            margin: 0;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text);
            background:
                linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,.02) 1px, transparent 1px);
            background-size: 44px 44px;
        }}

        button,
        input {{
            font: inherit;
        }}

        .shell {{
            width: min(1040px, calc(100% - 28px));
            margin: 0 auto;
            padding: 22px 0 28px;
        }}

        .topbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 16px;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}

        .logo {{
            width: 44px;
            height: 44px;
            border-radius: 15px;
            display: grid;
            place-items: center;
            color: #071017;
            font-weight: 950;
            letter-spacing: -.06em;
            background:
                linear-gradient(135deg, #6ff2ff 0%, #7c91ff 48%, #ff62ae 100%);
            box-shadow: 0 12px 36px rgba(66,216,255,.18);
        }}

        .brand strong {{
            display: block;
            font-size: 15px;
            letter-spacing: -.02em;
        }}

        .brand span {{
            display: block;
            margin-top: 2px;
            color: var(--muted);
            font-size: 12px;
        }}

        .meta-pill {{
            display: flex;
            align-items: center;
            gap: 8px;
            max-width: 48%;
            padding: 9px 12px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: rgba(255,255,255,.045);
            color: var(--muted);
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .meta-pill b {{
            color: var(--text);
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .hero {{
            position: relative;
            overflow: hidden;
            border: 1px solid var(--line);
            border-radius: 30px;
            background:
                linear-gradient(135deg, rgba(31,34,43,.96), rgba(23,25,32,.96)),
                radial-gradient(circle at 100% 0%, rgba(66,216,255,.18), transparent 28rem);
            box-shadow: var(--shadow);
        }}

        .hero::before {{
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(115deg, transparent 0 58%, rgba(66,216,255,.08) 58% 59%, transparent 59% 100%),
                linear-gradient(135deg, rgba(255,255,255,.07), transparent 34%);
        }}

        .hero-inner {{
            position: relative;
            display: grid;
            grid-template-columns: 1.1fr .9fr;
            gap: 18px;
            padding: 28px;
        }}

        .kicker {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 14px;
            color: var(--cyan);
            font-size: 12px;
            font-weight: 850;
            letter-spacing: .14em;
            text-transform: uppercase;
        }}

        .kicker::before {{
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: currentColor;
            box-shadow: 0 0 20px currentColor;
        }}

        h1 {{
            max-width: 620px;
            margin: 0;
            font-size: clamp(44px, 8vw, 86px);
            line-height: .9;
            letter-spacing: -.075em;
        }}

        .gradient-word {{
            color: transparent;
            background: linear-gradient(90deg, #f7fbff, #bfc7ff 46%, #ff8fc1);
            -webkit-background-clip: text;
            background-clip: text;
        }}

        .sub {{
            max-width: 590px;
            margin: 18px 0 0;
            color: #a7b0c2;
            font-size: 15px;
            line-height: 1.7;
        }}

        .target-card {{
            align-self: end;
            border: 1px solid var(--line);
            border-radius: 24px;
            background:
                linear-gradient(180deg, rgba(255,255,255,.075), rgba(255,255,255,.035)),
                rgba(12,13,18,.58);
            backdrop-filter: blur(18px);
            padding: 18px;
        }}

        .target-title {{
            color: var(--cyan);
            font-size: 12px;
            font-weight: 900;
            letter-spacing: .14em;
            text-transform: uppercase;
        }}

        .target-id {{
            margin-top: 12px;
            font-size: 19px;
            font-weight: 900;
            letter-spacing: -.03em;
            overflow-wrap: anywhere;
        }}

        .facts {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-top: 14px;
        }}

        .fact {{
            min-width: 0;
            padding: 10px;
            border: 1px solid var(--line);
            border-radius: 15px;
            background: rgba(255,255,255,.04);
        }}

        .fact span {{
            display: block;
            margin-bottom: 4px;
            color: var(--muted-2);
            font-size: 10px;
            font-weight: 900;
            letter-spacing: .12em;
            text-transform: uppercase;
        }}

        .fact b {{
            display: block;
            font-size: 13px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}

        .form-panel {{
            position: relative;
            margin-top: -1px;
            border: 1px solid var(--line);
            border-radius: 30px;
            background: rgba(20,22,29,.92);
            box-shadow: var(--shadow);
            padding: 22px;
        }}

        form {{
            display: grid;
            grid-template-columns: .8fr 1.2fr;
            gap: 18px;
        }}

        .card {{
            border: 1px solid var(--line);
            border-radius: var(--radius);
            background: linear-gradient(180deg, rgba(255,255,255,.055), rgba(255,255,255,.025));
            padding: 18px;
        }}

        .card-head {{
            display: flex;
            align-items: start;
            justify-content: space-between;
            gap: 14px;
            margin-bottom: 16px;
        }}

        .eyebrow {{
            color: var(--muted-2);
            font-size: 11px;
            font-weight: 900;
            letter-spacing: .16em;
            text-transform: uppercase;
        }}

        .card h2 {{
            margin: 5px 0 0;
            font-size: 18px;
            letter-spacing: -.04em;
        }}

        .step {{
            display: grid;
            place-items: center;
            width: 34px;
            height: 34px;
            flex: 0 0 auto;
            border: 1px solid rgba(66,216,255,.28);
            border-radius: 12px;
            background: rgba(66,216,255,.1);
            color: var(--cyan);
            font-weight: 950;
        }}

        .trackers {{
            display: grid;
            gap: 10px;
        }}

        .tracker {{
            position: relative;
            display: grid;
            grid-template-columns: auto 1fr auto;
            align-items: center;
            gap: 12px;
            min-height: 66px;
            padding: 13px;
            border: 1px solid var(--line);
            border-radius: 18px;
            background: rgba(10,12,17,.42);
            cursor: pointer;
        }}

        .tracker input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .tracker:has(input:checked) {{
            border-color: rgba(66,216,255,.62);
            background:
                linear-gradient(135deg, rgba(66,216,255,.16), rgba(109,145,255,.08)),
                rgba(10,12,17,.5);
        }}

        .tracker:has(input:disabled) {{
            opacity: .45;
            cursor: not-allowed;
        }}

        .badge {{
            width: 38px;
            height: 38px;
            border-radius: 13px;
            display: grid;
            place-items: center;
            color: #071017;
            font-weight: 950;
            background: linear-gradient(135deg, #72e6ff, #8f8cff);
        }}

        .badge.al {{
            background: linear-gradient(135deg, #66e8ff, #55b7ff);
        }}

        .tracker strong {{
            display: block;
            font-size: 14px;
            letter-spacing: -.025em;
        }}

        .tracker small {{
            display: block;
            margin-top: 3px;
            color: var(--muted);
            font-size: 12px;
        }}

        .check-dot {{
            width: 12px;
            height: 12px;
            border-radius: 999px;
            border: 1px solid var(--line-strong);
            background: rgba(255,255,255,.04);
        }}

        .tracker:has(input:checked) .check-dot {{
            border-color: transparent;
            background: var(--green);
            box-shadow: 0 0 18px rgba(57,217,138,.45);
        }}

        .status-grid {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 10px;
        }}

        .status-card {{
            position: relative;
            min-height: 122px;
            padding: 12px;
            border: 1px solid var(--line);
            border-radius: 18px;
            background: rgba(10,12,17,.38);
            cursor: pointer;
            overflow: hidden;
        }}

        .status-card::after {{
            content: "";
            position: absolute;
            inset: auto -20px -36px -20px;
            height: 62px;
            background: radial-gradient(circle, rgba(66,216,255,.14), transparent 68%);
            opacity: 0;
            transition: opacity .16s ease;
        }}

        .status-card input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .status-card:has(input:checked) {{
            border-color: rgba(66,216,255,.7);
            background: linear-gradient(180deg, rgba(66,216,255,.14), rgba(10,12,17,.48));
        }}

        .status-card:has(input:checked)::after {{
            opacity: 1;
        }}

        .status-mark {{
            width: 38px;
            height: 38px;
            border: 1px solid var(--line-strong);
            border-radius: 13px;
            display: grid;
            place-items: center;
            color: var(--text);
            background: rgba(255,255,255,.06);
            font-weight: 900;
        }}

        .status-copy {{
            position: relative;
            z-index: 1;
            display: block;
            margin-top: 18px;
        }}

        .status-copy strong {{
            display: block;
            font-size: 14px;
            line-height: 1.15;
            letter-spacing: -.035em;
        }}

        .status-copy small {{
            display: block;
            margin-top: 6px;
            color: var(--muted);
            font-size: 11px;
            line-height: 1.35;
        }}

        .lower-grid {{
            grid-column: 1 / -1;
            display: grid;
            grid-template-columns: .95fr 1.05fr;
            gap: 18px;
        }}

        .progress-row {{
            display: grid;
            grid-template-columns: 54px 1fr 54px;
            gap: 10px;
            margin-top: 14px;
        }}

        .step-btn,
        .submit {{
            border: 0;
            cursor: pointer;
        }}

        .step-btn {{
            min-height: 50px;
            border-radius: 16px;
            color: var(--text);
            background: rgba(255,255,255,.07);
            border: 1px solid var(--line);
            font-size: 22px;
            font-weight: 900;
        }}

        .step-btn:active {{
            transform: translateY(1px);
        }}

        .progress-row input {{
            width: 100%;
            min-height: 50px;
            border: 1px solid var(--line);
            border-radius: 16px;
            background: rgba(7,8,12,.62);
            color: var(--text);
            text-align: center;
            font-size: 19px;
            font-weight: 900;
            outline: none;
        }}

        .score-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 12px;
        }}

        .score-top strong {{
            font-size: 14px;
        }}

        .score-display {{
            color: var(--text);
            font-weight: 950;
        }}

        .score-grid {{
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 8px;
        }}

        .score-btn {{
            min-height: 44px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: rgba(10,12,17,.48);
            color: var(--muted);
            cursor: pointer;
            font-weight: 850;
        }}

        .score-btn.active {{
            border-color: rgba(255,95,162,.65);
            color: #fff;
            background: linear-gradient(135deg, rgba(255,187,122,.95), rgba(255,95,162,.95));
        }}

        .footer-row {{
            grid-column: 1 / -1;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding-top: 2px;
        }}

        .hint {{
            color: var(--muted);
            font-size: 12px;
            line-height: 1.5;
        }}

        .submit {{
            min-height: 54px;
            min-width: 190px;
            padding: 0 24px;
            border-radius: 18px;
            color: #061016;
            background: linear-gradient(135deg, #6df2ff, #8d8dff 52%, #ff66aa);
            font-weight: 950;
            box-shadow: 0 16px 40px rgba(66,216,255,.18);
        }}

        .submit:disabled {{
            opacity: .62;
            cursor: wait;
        }}

        .toast {{
            display: none;
            margin-top: 14px;
            padding: 13px 14px;
            border: 1px solid var(--line);
            border-radius: 16px;
            background: rgba(255,255,255,.055);
            color: var(--text);
            font-size: 13px;
            line-height: 1.45;
        }}

        .toast.show {{
            display: block;
        }}

        .toast.success {{
            border-color: rgba(57,217,138,.38);
            background: rgba(57,217,138,.1);
        }}

        .toast.error {{
            border-color: rgba(255,107,122,.42);
            background: rgba(255,107,122,.1);
        }}

        @media (max-width: 820px) {{
            .shell {{
                width: min(100% - 18px, 640px);
                padding-top: 10px;
            }}

            .topbar {{
                align-items: flex-start;
            }}

            .meta-pill {{
                max-width: 54%;
            }}

            .hero,
            .form-panel {{
                border-radius: 24px;
            }}

            .hero-inner,
            form,
            .lower-grid {{
                grid-template-columns: 1fr;
            }}

            .hero-inner {{
                padding: 22px;
            }}

            .target-card {{
                align-self: stretch;
            }}

            .status-grid {{
                grid-template-columns: 1fr;
            }}

            .status-card {{
                min-height: auto;
                display: flex;
                align-items: center;
                gap: 12px;
            }}

            .status-copy {{
                margin-top: 0;
            }}

            .footer-row {{
                position: sticky;
                bottom: 10px;
                z-index: 5;
                display: grid;
                grid-template-columns: 1fr;
                padding: 12px;
                border: 1px solid var(--line);
                border-radius: 22px;
                background: rgba(16,17,22,.86);
                backdrop-filter: blur(18px);
            }}

            .submit {{
                width: 100%;
            }}
        }}

        @media (max-width: 440px) {{
            .score-grid {{
                grid-template-columns: repeat(4, 1fr);
            }}

            .facts {{
                grid-template-columns: 1fr;
            }}

            h1 {{
                font-size: 46px;
            }}

            .form-panel {{
                padding: 14px;
            }}

            .card {{
                padding: 15px;
            }}
        }}
    </style>
</head>
<body>
    <main class="shell">
        <header class="topbar">
            <div class="brand">
                <div class="logo">AS</div>
                <div>
                    <strong>AniSync</strong>
                    <span>Stremio list control</span>
                </div>
            </div>

            <div class="meta-pill" title="{safe_meta_id}">
                <span>Target</span>
                <b>{safe_meta_id}</b>
            </div>
        </header>

        <section class="hero">
            <div class="hero-inner">
                <div>
                    <div class="kicker">Manual sync</div>
                    <h1>Track it before the ending song.</h1>
                    <p class="sub">
                        Update status, episode progress, and score from the page Stremio opens.
                        Only selected services are touched.
                    </p>
                </div>

                <aside class="target-card">
                    <div class="target-title">Current entry</div>
                    <div class="target-id">{safe_meta_id}</div>

                    <div class="facts">
                        <div class="fact">
                            <span>Type</span>
                            <b>{safe_type}</b>
                        </div>
                        <div class="fact">
                            <span>Episode</span>
                            <b>{default_progress}</b>
                        </div>
                        <div class="fact">
                            <span>MAL</span>
                            <b>{html.escape(str(ids.get("mal_id") or "not found"))}</b>
                        </div>
                        <div class="fact">
                            <span>AniList</span>
                            <b>{html.escape(str(ids.get("anilist_id") or "not found"))}</b>
                        </div>
                    </div>
                </aside>
            </div>
        </section>

        <section class="form-panel">
            <form id="sync-form" action="/{safe_user_id}/manage/update" method="post">
                <input type="hidden" name="meta_id" value="{safe_meta_id}">

                <section class="card">
                    <div class="card-head">
                        <div>
                            <div class="eyebrow">Step 01</div>
                            <h2>Send to</h2>
                        </div>
                        <div class="step">1</div>
                    </div>

                    <div class="trackers">
                        <label class="tracker">
                            <input type="checkbox" name="provider_mal" value="1" {mal_checked} {mal_disabled}>
                            <span class="badge">M</span>
                            <span>
                                <strong>MyAnimeList</strong>
                                <small>{"Ready" if mal_available else "Unavailable for this entry"}</small>
                            </span>
                            <span class="check-dot"></span>
                        </label>

                        <label class="tracker">
                            <input type="checkbox" name="provider_anilist" value="1" {anilist_checked} {anilist_disabled}>
                            <span class="badge al">A</span>
                            <span>
                                <strong>AniList</strong>
                                <small>{"Ready" if anilist_available else "Unavailable for this entry"}</small>
                            </span>
                            <span class="check-dot"></span>
                        </label>
                    </div>
                </section>

                <section class="card">
                    <div class="card-head">
                        <div>
                            <div class="eyebrow">Step 02</div>
                            <h2>Anime state</h2>
                        </div>
                        <div class="step">2</div>
                    </div>

                    <div class="status-grid">
                        {status_buttons}
                    </div>
                </section>

                <section class="lower-grid">
                    <section class="card">
                        <div class="card-head">
                            <div>
                                <div class="eyebrow">Step 03</div>
                                <h2>Progress</h2>
                            </div>
                            <div class="step">3</div>
                        </div>

                        <div class="progress-row">
                            <button class="step-btn" type="button" data-progress-step="-1">−</button>
                            <input id="progress" name="progress" type="number" min="0" step="1" value="{default_progress}" inputmode="numeric">
                            <button class="step-btn" type="button" data-progress-step="1">+</button>
                        </div>
                    </section>

                    <section class="card">
                        <div class="card-head">
                            <div>
                                <div class="eyebrow">Step 04</div>
                                <h2>Your score</h2>
                            </div>
                            <div class="step">4</div>
                        </div>

                        <input id="score" name="score" type="hidden" value="">

                        <div class="score-top">
                            <strong>Rating</strong>
                            <span class="score-display" data-score-display>— / 10</span>
                        </div>

                        <div class="score-grid">
                            <button type="button" class="score-btn active" data-score="">No score</button>
                            <button type="button" class="score-btn" data-score="1">1</button>
                            <button type="button" class="score-btn" data-score="2">2</button>
                            <button type="button" class="score-btn" data-score="3">3</button>
                            <button type="button" class="score-btn" data-score="4">4</button>
                            <button type="button" class="score-btn" data-score="5">5</button>
                            <button type="button" class="score-btn" data-score="6">6</button>
                            <button type="button" class="score-btn" data-score="7">7</button>
                            <button type="button" class="score-btn" data-score="8">8</button>
                            <button type="button" class="score-btn" data-score="9">9</button>
                            <button type="button" class="score-btn" data-score="10">10</button>
                        </div>
                    </section>
                </section>

                <div class="footer-row">
                    <div class="hint">
                        AniSync updates the selected trackers only. Disabled trackers are either disconnected or not resolved for this anime.
                    </div>
                    <button class="submit" type="submit">Update list</button>
                </div>
            </form>

            <div class="toast" data-result></div>
        </section>
    </main>

    <script>
        const form = document.getElementById("sync-form");
        const result = document.querySelector("[data-result]");
        const progressInput = document.getElementById("progress");
        const scoreInput = document.getElementById("score");
        const scoreDisplay = document.querySelector("[data-score-display]");
        const submit = form.querySelector(".submit");

        document.querySelectorAll("[data-progress-step]").forEach((button) => {{
            button.addEventListener("click", () => {{
                const step = Number(button.dataset.progressStep || 0);
                const current = Number(progressInput.value || 0);
                progressInput.value = Math.max(0, current + step);
            }});
        }});

        document.querySelectorAll("[data-score]").forEach((button) => {{
            button.addEventListener("click", () => {{
                const value = button.dataset.score;
                scoreInput.value = value;

                document.querySelectorAll("[data-score]").forEach((item) => {{
                    item.classList.toggle("active", item === button);
                }});

                scoreDisplay.textContent = value ? value + " / 10" : "— / 10";
            }});
        }});

        form.addEventListener("submit", async (event) => {{
            event.preventDefault();

            result.className = "toast";
            result.textContent = "";
            submit.disabled = true;
            submit.textContent = "Updating…";

            try {{
                const response = await fetch(form.action, {{
                    method: "POST",
                    body: new FormData(form),
                    headers: {{
                        "Accept": "application/json"
                    }}
                }});

                const payload = await response.json();
                const ok = response.ok && payload.status === "success";

                result.textContent = payload.message || (ok ? "Updated." : "Update failed.");
                result.className = "toast show " + (ok ? "success" : "error");
            }} catch (error) {{
                result.textContent = "Network error. Try again.";
                result.className = "toast show error";
            }} finally {{
                submit.disabled = false;
                submit.textContent = "Update list";
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