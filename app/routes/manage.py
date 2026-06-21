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

    safe_user_id = html.escape(urllib.parse.quote(user_id, safe=""), quote=True)
    form_action = f"/{safe_user_id}/manage/update"

    safe_meta_id = html.escape(meta_id, quote=True)
    safe_type = html.escape(content_type, quote=True)
    safe_kitsu_id = html.escape(str(ids.get("kitsu_id") or "not found"), quote=True)
    safe_mal_id = html.escape(str(ids.get("mal_id") or "not found"), quote=True)
    safe_anilist_id = html.escape(str(ids.get("anilist_id") or "not found"), quote=True)
    safe_simkl_id = html.escape(str(ids.get("simkl_id") or "not found"), quote=True)

    default_progress = episode or 0

    mal_has_token = bool(user.get("mal_access_token"))
    mal_enabled = bool(user.get("mal_enabled", True))
    mal_has_id = bool(ids.get("mal_id"))
    mal_available = mal_has_token and mal_enabled and mal_has_id

    anilist_has_token = bool(user.get("anilist_token"))
    anilist_enabled = bool(user.get("anilist_enabled", True))
    anilist_has_id = bool(ids.get("anilist_id"))
    anilist_available = anilist_has_token and anilist_enabled and anilist_has_id

    def provider_note(has_token: bool, enabled: bool, has_id: bool) -> str:
        if not has_token:
            return "Not connected"
        if not enabled:
            return "Disabled"
        if not has_id:
            return "No ID found"
        return "Ready"

    mal_checked = "checked" if mal_available else ""
    anilist_checked = "checked" if anilist_available else ""
    mal_disabled = "" if mal_available else "disabled"
    anilist_disabled = "" if anilist_available else "disabled"
    mal_state = "is-ready" if mal_available else "is-muted"
    anilist_state = "is-ready" if anilist_available else "is-muted"
    mal_note = html.escape(provider_note(mal_has_token, mal_enabled, mal_has_id), quote=True)
    anilist_note = html.escape(provider_note(anilist_has_token, anilist_enabled, anilist_has_id), quote=True)

    status_meta = {
        "watching": {"eyebrow": "Now airing", "icon": "▶", "tone": "cyan"},
        "plan_to_watch": {"eyebrow": "Saved queue", "icon": "＋", "tone": "violet"},
        "completed": {"eyebrow": "Archive it", "icon": "✓", "tone": "green"},
        "on_hold": {"eyebrow": "Paused", "icon": "Ⅱ", "tone": "amber"},
        "dropped": {"eyebrow": "Stopped", "icon": "×", "tone": "red"},
    }

    status_cards = ""
    for key, data in STATUS_MAP.items():
        meta = status_meta.get(key, {})
        checked = "checked" if key == "watching" else ""
        status_cards += f'''
            <label class="status-card tone-{html.escape(meta.get("tone", "cyan"), quote=True)}">
                <input type="radio" name="status" value="{html.escape(key, quote=True)}" {checked}>
                <span class="status-glow"></span>
                <span class="status-icon">{html.escape(meta.get("icon", "•"), quote=True)}</span>
                <span>
                    <strong>{html.escape(data["label"], quote=True)}</strong>
                    <small>{html.escape(meta.get("eyebrow", ""), quote=True)}</small>
                </span>
            </label>
        '''

    score_buttons = '''<button class="score-dot is-clear is-active" type="button" data-score="" aria-label="No score">—</button>'''
    for value in range(1, 11):
        score_buttons += f'''<button class="score-dot" type="button" data-score="{value}" aria-label="Score {value} out of 10">★</button>'''

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AniSync · Track</title>
    <style>
        :root {{
            color-scheme: dark;
            --bg: #121415;
            --panel: #1b1d1f;
            --panel-2: #222528;
            --line: rgba(255, 255, 255, .105);
            --line-strong: rgba(255, 255, 255, .18);
            --text: #f5f7fb;
            --muted: #8d98a8;
            --soft: #b8c0cf;
            --cyan: #13bfe7;
            --cyan-2: #0b8fa8;
            --green: #18d99a;
            --amber: #ffb84d;
            --red: #ff5d78;
            --violet: #9b7cff;
            --shadow: 0 28px 80px rgba(0, 0, 0, .46);
            --radius: 24px;
        }}

        * {{ box-sizing: border-box; }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 0 0, rgba(19, 191, 231, .16), transparent 32rem),
                radial-gradient(circle at 100% 100%, rgba(155, 124, 255, .10), transparent 30rem),
                linear-gradient(135deg, #121717 0%, #111113 46%, #151318 100%);
            display: grid;
            place-items: center;
            padding: clamp(18px, 4vw, 52px);
        }}

        .shell {{ width: min(880px, 100%); }}

        .card {{
            position: relative;
            overflow: hidden;
            background:
                linear-gradient(180deg, rgba(255, 255, 255, .055), rgba(255, 255, 255, .025)),
                var(--panel);
            border: 1px solid var(--line);
            border-radius: var(--radius);
            box-shadow: var(--shadow);
            padding: clamp(22px, 4.8vw, 46px);
        }}

        .card::before {{
            content: "";
            position: absolute;
            inset: 0 0 auto;
            height: 1px;
            background: linear-gradient(90deg, transparent, rgba(19, 191, 231, .55), transparent);
        }}

        .card::after {{
            content: "";
            position: absolute;
            width: 260px;
            height: 260px;
            right: -110px;
            top: -110px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(19, 191, 231, .14), transparent 68%);
            pointer-events: none;
        }}

        .topbar {{
            position: relative;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 18px;
            align-items: start;
            margin-bottom: 28px;
        }}

        .brand-kicker {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: .08em;
            text-transform: uppercase;
            color: #0b1417;
            background: linear-gradient(135deg, var(--cyan), #e8fbff);
            border-radius: 999px;
            padding: 6px 10px;
            box-shadow: 0 0 26px rgba(19, 191, 231, .18);
        }}

        h1 {{
            margin: 12px 0 8px;
            font-size: clamp(42px, 7vw, 72px);
            line-height: .86;
            letter-spacing: -.07em;
        }}

        .subtitle {{
            margin: 0;
            max-width: 56ch;
            color: var(--soft);
            font-size: 14px;
            line-height: 1.7;
        }}

        .orbit {{
            position: relative;
            width: 112px;
            height: 112px;
            border: 1px solid var(--line);
            border-radius: 28px;
            background: linear-gradient(145deg, rgba(255, 255, 255, .06), rgba(255, 255, 255, .02));
            display: grid;
            place-items: center;
        }}

        .orbit-ring {{
            position: absolute;
            width: 70px;
            height: 70px;
            border: 1px dashed rgba(19, 191, 231, .5);
            border-radius: 999px;
            animation: spin 16s linear infinite;
        }}

        .orbit-dot {{
            position: absolute;
            width: 10px;
            height: 10px;
            top: -5px;
            left: calc(50% - 5px);
            border-radius: 999px;
            background: var(--cyan);
            box-shadow: 0 0 18px var(--cyan);
        }}

        .orbit-core {{
            width: 42px;
            height: 42px;
            border-radius: 14px;
            display: grid;
            place-items: center;
            font-weight: 950;
            color: white;
            background: linear-gradient(135deg, #2c3137, #16191d);
            border: 1px solid var(--line-strong);
        }}

        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}

        form {{ position: relative; z-index: 1; }}

        .grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 18px;
        }}

        .section {{
            background: rgba(255, 255, 255, .026);
            border: 1px solid var(--line);
            border-radius: 20px;
            padding: 18px;
        }}

        .section-head {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            align-items: end;
            margin-bottom: 14px;
        }}

        .eyebrow {{
            margin: 0 0 6px;
            color: var(--muted);
            font-size: 11px;
            font-weight: 800;
            letter-spacing: .14em;
            text-transform: uppercase;
        }}

        h2 {{ margin: 0; font-size: 18px; letter-spacing: -.03em; }}

        .mini {{ margin: 0; color: var(--muted); font-size: 12px; }}

        .providers {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }}

        .provider {{
            position: relative;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            min-height: 70px;
            padding: 14px;
            border-radius: 17px;
            border: 1px solid var(--line);
            background: var(--panel-2);
            cursor: pointer;
            transition: transform .18s ease, border-color .18s ease, background .18s ease;
        }}

        .provider:hover {{ transform: translateY(-1px); border-color: rgba(19, 191, 231, .32); }}
        .provider.is-muted {{ opacity: .52; cursor: not-allowed; }}

        .provider-main {{ display: flex; align-items: center; gap: 11px; }}

        .provider-badge {{
            width: 28px;
            height: 28px;
            border-radius: 9px;
            display: grid;
            place-items: center;
            font-size: 12px;
            font-weight: 950;
            background: #2d3748;
            color: white;
        }}

        .provider strong {{ display: block; font-size: 13px; }}
        .provider small {{ display: block; margin-top: 2px; color: var(--muted); font-size: 11px; }}

        .provider input {{ position: absolute; opacity: 0; pointer-events: none; }}

        .check {{
            width: 38px;
            height: 24px;
            border-radius: 999px;
            background: #34383d;
            border: 1px solid var(--line);
            padding: 3px;
            transition: .18s ease;
        }}

        .check::before {{
            content: "";
            display: block;
            width: 16px;
            height: 16px;
            border-radius: 999px;
            background: #777f8c;
            transition: .18s ease;
        }}

        .provider input:checked + .check {{ background: rgba(19, 191, 231, .26); border-color: rgba(19, 191, 231, .55); }}
        .provider input:checked + .check::before {{ transform: translateX(14px); background: var(--cyan); box-shadow: 0 0 14px rgba(19, 191, 231, .6); }}

        .status-grid {{
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 10px;
        }}

        .status-card {{
            position: relative;
            overflow: hidden;
            min-height: 86px;
            border-radius: 17px;
            border: 1px solid var(--line);
            background: var(--panel-2);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 10px;
            padding: 13px;
            cursor: pointer;
            transition: transform .18s ease, border-color .18s ease, background .18s ease;
        }}

        .status-card:hover {{ transform: translateY(-2px); border-color: rgba(255, 255, 255, .23); }}
        .status-card input {{ position: absolute; opacity: 0; }}

        .status-icon {{
            width: 28px;
            height: 28px;
            border-radius: 10px;
            display: grid;
            place-items: center;
            background: rgba(255, 255, 255, .07);
            font-weight: 900;
        }}

        .status-card strong {{ display: block; font-size: 12px; }}
        .status-card small {{ display: block; margin-top: 2px; color: var(--muted); font-size: 10px; }}
        .status-glow {{ position: absolute; inset: auto 10px -32px; height: 54px; filter: blur(24px); opacity: 0; transition: .18s ease; }}
        .tone-cyan .status-glow {{ background: var(--cyan); }}
        .tone-violet .status-glow {{ background: var(--violet); }}
        .tone-green .status-glow {{ background: var(--green); }}
        .tone-amber .status-glow {{ background: var(--amber); }}
        .tone-red .status-glow {{ background: var(--red); }}
        .status-card:has(input:checked) {{ border-color: rgba(19, 191, 231, .62); background: linear-gradient(180deg, rgba(19, 191, 231, .14), rgba(255, 255, 255, .035)); }}
        .status-card:has(input:checked) .status-glow {{ opacity: .42; }}

        .controls {{
            display: grid;
            grid-template-columns: 220px 1fr;
            gap: 16px;
            align-items: stretch;
        }}

        .progress-box {{
            display: grid;
            grid-template-columns: 44px 1fr 44px;
            gap: 10px;
            align-items: center;
            padding: 12px;
            border-radius: 18px;
            background: var(--panel-2);
            border: 1px solid var(--line);
        }}

        .round-btn {{
            width: 44px;
            height: 44px;
            border: 0;
            border-radius: 14px;
            background: rgba(255, 255, 255, .075);
            color: var(--text);
            font-size: 22px;
            font-weight: 800;
            cursor: pointer;
        }}

        .round-btn:active {{ transform: scale(.96); }}

        .progress-input {{
            width: 100%;
            border: 0;
            outline: 0;
            background: transparent;
            color: var(--text);
            text-align: center;
            font-size: 28px;
            font-weight: 900;
            letter-spacing: -.04em;
        }}

        .score-panel {{
            min-width: 0;
            padding: 13px;
            border-radius: 18px;
            border: 1px solid var(--line);
            background: var(--panel-2);
        }}

        .score-row {{
            display: flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }}

        .score-dot {{
            width: 31px;
            height: 34px;
            border: 0;
            background: transparent;
            color: #555b64;
            font-size: 25px;
            line-height: 1;
            cursor: pointer;
            transition: transform .14s ease, color .14s ease, text-shadow .14s ease;
        }}

        .score-dot:hover {{ transform: translateY(-2px) scale(1.05); color: var(--cyan); }}
        .score-dot.is-on {{ color: var(--cyan); text-shadow: 0 0 16px rgba(19, 191, 231, .42); }}
        .score-dot.is-clear {{
            width: 34px;
            border-radius: 11px;
            background: rgba(255, 255, 255, .06);
            color: var(--soft);
            font-size: 18px;
            font-weight: 900;
        }}
        .score-dot.is-clear.is-active {{ color: var(--cyan); }}

        .score-readout {{
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 12px;
        }}

        .score-readout strong {{ color: var(--cyan); font-size: 18px; }}

        .sync-row {{
            display: grid;
            grid-template-columns: 1fr 220px;
            gap: 16px;
            align-items: center;
            margin-top: 18px;
        }}

        .sync-copy {{
            display: flex;
            align-items: center;
            gap: 12px;
            color: var(--soft);
            font-size: 13px;
        }}

        .sync-pulse {{
            width: 42px;
            height: 42px;
            border-radius: 14px;
            background: rgba(19, 191, 231, .12);
            border: 1px solid rgba(19, 191, 231, .24);
            display: grid;
            place-items: center;
        }}

        .sync-pulse span {{
            width: 10px;
            height: 10px;
            border-radius: 999px;
            background: var(--cyan);
            box-shadow: 0 0 0 rgba(19, 191, 231, .45);
            animation: pulse 1.8s infinite;
        }}

        @keyframes pulse {{
            70% {{ box-shadow: 0 0 0 16px rgba(19, 191, 231, 0); }}
            100% {{ box-shadow: 0 0 0 0 rgba(19, 191, 231, 0); }}
        }}

        .submit {{
            position: relative;
            overflow: hidden;
            width: 100%;
            height: 54px;
            border: 0;
            border-radius: 15px;
            background: linear-gradient(135deg, var(--cyan-2), var(--cyan));
            color: white;
            font-weight: 950;
            cursor: pointer;
            box-shadow: 0 14px 34px rgba(19, 191, 231, .18);
        }}

        .submit::before {{
            content: "";
            position: absolute;
            inset: 0 auto 0 -55%;
            width: 45%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, .32), transparent);
            transform: skewX(-18deg);
            transition: .45s ease;
        }}

        .submit:hover::before {{ left: 115%; }}
        .submit:active {{ transform: translateY(1px); }}

        details {{ margin-top: 18px; color: var(--muted); font-size: 12px; }}
        summary {{ cursor: pointer; width: fit-content; }}

        .id-grid {{
            margin-top: 12px;
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
        }}

        .id-chip {{
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 10px;
            background: rgba(255, 255, 255, .026);
            min-width: 0;
        }}

        .id-chip b {{ display: block; color: var(--soft); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }}
        .id-chip span {{ display: block; margin-top: 5px; overflow-wrap: anywhere; color: var(--muted); }}

        @media (max-width: 760px) {{
            body {{ padding: 14px; place-items: start center; }}
            .topbar {{ grid-template-columns: 1fr; }}
            .orbit {{ display: none; }}
            .providers, .controls, .sync-row {{ grid-template-columns: 1fr; }}
            .status-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
            .id-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
        }}

        @media (max-width: 430px) {{
            .card {{ padding: 20px; border-radius: 20px; }}
            .status-grid, .id-grid {{ grid-template-columns: 1fr; }}
            h1 {{ font-size: 46px; }}
        }}
    </style>
</head>
<body>
    <main class="shell">
        <section class="card">
            <div class="topbar">
                <div>
                    <span class="brand-kicker">AniSync · MAL / AniList</span>
                    <h1>Track<br>without friction.</h1>
                    <p class="subtitle">A compact control panel for Stremio: choose services, set status, adjust progress, rate the anime, then sync it in one clean action.</p>
                </div>

                <div class="orbit" aria-hidden="true">
                    <div class="orbit-ring"><span class="orbit-dot"></span></div>
                    <div class="orbit-core">AS</div>
                </div>
            </div>

            <form id="trackForm" method="post" action="{form_action}">
                <input type="hidden" name="meta_id" value="{safe_meta_id}">

                <div class="grid">
                    <section class="section">
                        <div class="section-head">
                            <div>
                                <p class="eyebrow">Send to services</p>
                                <h2>Choose trackers</h2>
                            </div>
                            <p class="mini">Target: {safe_type} · {safe_meta_id}</p>
                        </div>

                        <div class="providers">
                            <label class="provider {mal_state}">
                                <span class="provider-main">
                                    <span class="provider-badge">M</span>
                                    <span><strong>MyAnimeList</strong><small>{mal_note}</small></span>
                                </span>
                                <input type="checkbox" name="provider_mal" value="1" {mal_checked} {mal_disabled}>
                                <span class="check" aria-hidden="true"></span>
                            </label>

                            <label class="provider {anilist_state}">
                                <span class="provider-main">
                                    <span class="provider-badge">A</span>
                                    <span><strong>AniList</strong><small>{anilist_note}</small></span>
                                </span>
                                <input type="checkbox" name="provider_anilist" value="1" {anilist_checked} {anilist_disabled}>
                                <span class="check" aria-hidden="true"></span>
                            </label>
                        </div>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <div>
                                <p class="eyebrow">Status</p>
                                <h2>Where should this anime live?</h2>
                            </div>
                            <p class="mini">One state only</p>
                        </div>

                        <div class="status-grid">
                            {status_cards}
                        </div>
                    </section>

                    <section class="section">
                        <div class="section-head">
                            <div>
                                <p class="eyebrow">Progress & score</p>
                                <h2>Episode checkpoint</h2>
                            </div>
                            <p class="mini">Opened from episode {default_progress}</p>
                        </div>

                        <div class="controls">
                            <div class="progress-box">
                                <button class="round-btn" type="button" data-step="-1" aria-label="Decrease progress">−</button>
                                <input class="progress-input" id="progressInput" name="progress" type="number" min="0" inputmode="numeric" value="{default_progress}">
                                <button class="round-btn" type="button" data-step="1" aria-label="Increase progress">+</button>
                            </div>

                            <div class="score-panel">
                                <input type="hidden" id="scoreInput" name="score" value="">
                                <div class="score-row" aria-label="Score selector">
                                    {score_buttons}
                                </div>
                                <p class="score-readout"><strong id="scoreText">—</strong> / 10 <span id="scoreHint">No score selected</span></p>
                            </div>
                        </div>

                        <div class="sync-row">
                            <div class="sync-copy">
                                <span class="sync-pulse"><span></span></span>
                                <span>Updates selected trackers only. Keep one service unchecked if you want a split-list edit.</span>
                            </div>
                            <button class="submit" id="submitBtn" type="submit">Sync Progress</button>
                        </div>
                    </section>
                </div>

                <details>
                    <summary>Technical details</summary>
                    <div class="id-grid">
                        <div class="id-chip"><b>ID</b><span>{safe_meta_id}</span></div>
                        <div class="id-chip"><b>Kitsu</b><span>{safe_kitsu_id}</span></div>
                        <div class="id-chip"><b>MAL</b><span>{safe_mal_id}</span></div>
                        <div class="id-chip"><b>AniList</b><span>{safe_anilist_id}</span></div>
                        <div class="id-chip"><b>Simkl</b><span>{safe_simkl_id}</span></div>
                    </div>
                </details>
            </form>
        </section>
    </main>

    <script>
        const progressInput = document.getElementById('progressInput');

        document.querySelectorAll('[data-step]').forEach((button) => {{
            button.addEventListener('click', () => {{
                const current = parseInt(progressInput.value || '0', 10);
                const next = Math.max(0, current + parseInt(button.dataset.step, 10));
                progressInput.value = next;

                progressInput.animate([
                    {{ transform: 'scale(1)' }},
                    {{ transform: 'scale(1.08)' }},
                    {{ transform: 'scale(1)' }}
                ], {{ duration: 180, easing: 'ease-out' }});
            }});
        }});

        const scoreInput = document.getElementById('scoreInput');
        const scoreText = document.getElementById('scoreText');
        const scoreHint = document.getElementById('scoreHint');
        const scoreDots = [...document.querySelectorAll('.score-dot')];

        function paintScore(score) {{
            scoreDots.forEach((dot) => {{
                const value = dot.dataset.score;
                dot.classList.toggle('is-active', value === score);
                dot.classList.toggle('is-on', score !== '' && value !== '' && Number(value) <= Number(score));
            }});

            scoreInput.value = score;
            scoreText.textContent = score === '' ? '—' : score;
            scoreHint.textContent = score === '' ? 'No score selected' : score >= 8 ? 'Strong pick' : score >= 5 ? 'Mid-season read' : 'Low score';
        }}

        scoreDots.forEach((dot) => {{
            dot.addEventListener('click', () => paintScore(dot.dataset.score));
        }});

        document.getElementById('trackForm').addEventListener('submit', () => {{
            const btn = document.getElementById('submitBtn');
            btn.textContent = 'Syncing…';
            btn.disabled = true;
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