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


@manage_bp.route('/<user_id>/manage', methods=['GET'])
@rate_limit(limit=60, period_seconds=60)
async def manage_page(user_id: str):
    if not is_valid_user_id(user_id):
        return 'Invalid user.', 400

    user = get_user(user_id)
    if not user:
        return 'Unknown user.', 404

    raw_id = request.args.get('id', '')
    content_type = request.args.get('type', 'series')
    meta_id, episode = _parse_content_id(raw_id)
    ids = await _resolve_ids(meta_id)

    safe_form_action = html.escape(f"/{urllib.parse.quote(user_id, safe='')}/manage/update", quote=True)
    safe_meta_id = html.escape(meta_id, quote=True)
    safe_type = html.escape(content_type, quote=True)
    safe_kitsu_id = html.escape(str(ids.get('kitsu_id') or 'not found'), quote=True)
    safe_mal_id = html.escape(str(ids.get('mal_id') or 'not found'), quote=True)
    safe_anilist_id = html.escape(str(ids.get('anilist_id') or 'not found'), quote=True)
    safe_simkl_id = html.escape(str(ids.get('simkl_id') or 'not found'), quote=True)
    safe_default_progress = html.escape(str(episode or 0), quote=True)

    mal_has_token = bool(user.get('mal_access_token'))
    mal_enabled = bool(user.get('mal_enabled', True))
    mal_has_id = bool(ids.get('mal_id'))
    mal_available = mal_has_token and mal_enabled and mal_has_id

    anilist_has_token = bool(user.get('anilist_token'))
    anilist_enabled = bool(user.get('anilist_enabled', True))
    anilist_has_id = bool(ids.get('anilist_id'))
    anilist_available = anilist_has_token and anilist_enabled and anilist_has_id

    def provider_note(has_token: bool, enabled: bool, has_id: bool) -> str:
        if not has_token:
            return 'Not connected'
        if not enabled:
            return 'Disabled'
        if not has_id:
            return 'No ID found'
        return 'Ready'

    mal_note = html.escape(provider_note(mal_has_token, mal_enabled, mal_has_id), quote=True)
    anilist_note = html.escape(provider_note(anilist_has_token, anilist_enabled, anilist_has_id), quote=True)

    mal_checked = 'checked' if mal_available else ''
    anilist_checked = 'checked' if anilist_available else ''
    mal_disabled = '' if mal_available else 'disabled'
    anilist_disabled = '' if anilist_available else 'disabled'
    mal_state = '' if mal_available else 'is-disabled'
    anilist_state = '' if anilist_available else 'is-disabled'

    status_meta = {
        'watching': {'hint': 'Currently active', 'icon': '▶', 'tone': 'cyan'},
        'plan_to_watch': {'hint': 'Queue for later', 'icon': '+', 'tone': 'violet'},
        'completed': {'hint': 'Finished', 'icon': '✓', 'tone': 'green'},
        'on_hold': {'hint': 'Paused', 'icon': 'Ⅱ', 'tone': 'amber'},
        'dropped': {'hint': 'Stopped', 'icon': '×', 'tone': 'red'},
    }

    status_cards = ''
    for key, data in STATUS_MAP.items():
        meta = status_meta.get(key, {})
        safe_key = html.escape(key, quote=True)
        safe_label = html.escape(data['label'], quote=True)
        safe_hint = html.escape(meta.get('hint', ''), quote=True)
        safe_icon = html.escape(meta.get('icon', '•'), quote=True)
        safe_tone = html.escape(meta.get('tone', 'cyan'), quote=True)
        checked = 'checked' if key == 'watching' else ''
        status_cards += f'''
                        <label class="status-card status-card--{safe_tone}">
                            <input type="radio" name="status" value="{safe_key}" {checked} />
                            <span class="status-card__glow" aria-hidden="true"></span>
                            <span class="status-card__icon">{safe_icon}</span>
                            <span>
                                <strong>{safe_label}</strong>
                                <small>{safe_hint}</small>
                            </span>
                        </label>'''

    score_controls = '''
                        <label class="score-pill">
                            <input type="radio" name="score" value="" checked />
                            <span>No score</span>
                        </label>'''
    for value in range(1, 11):
        score_controls += f'''
                        <label class="score-pill">
                            <input type="radio" name="score" value="{value}" />
                            <span>{value}</span>
                        </label>'''

    page = """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <meta name="color-scheme" content="dark" />
    <title>AniSync · Manage Progress</title>
    <style>
        :root {
            --bg: #070912;
            --bg-2: #0d1020;
            --panel: rgba(17, 22, 42, .82);
            --panel-strong: rgba(21, 27, 52, .94);
            --line: rgba(255,255,255,.11);
            --line-strong: rgba(255,255,255,.18);
            --text: #f6f7fb;
            --muted: #9ca6bd;
            --muted-2: #6f7a92;
            --cyan: #52e8ff;
            --violet: #9c7cff;
            --pink: #ff5fb7;
            --green: #7af0b4;
            --amber: #ffd166;
            --red: #ff6b7a;
            --shadow: 0 24px 80px rgba(0,0,0,.45);
            --radius-xl: 32px;
            --radius-lg: 24px;
            --radius-md: 18px;
            --radius-sm: 13px;
            --tap: 48px;
        }

        * { box-sizing: border-box; }

        html {
            min-height: 100%;
            background: var(--bg);
        }

        body {
            min-height: 100vh;
            margin: 0;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 12% 8%, rgba(82,232,255,.18), transparent 31rem),
                radial-gradient(circle at 86% 6%, rgba(156,124,255,.22), transparent 29rem),
                radial-gradient(circle at 70% 92%, rgba(255,95,183,.12), transparent 28rem),
                linear-gradient(135deg, #070912 0%, #0a0d19 48%, #101429 100%);
            overflow-x: hidden;
        }

        body::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(rgba(255,255,255,.028) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255,255,255,.024) 1px, transparent 1px);
            background-size: 42px 42px;
            mask-image: linear-gradient(to bottom, rgba(0,0,0,.75), transparent 78%);
        }

        button, input { font: inherit; }

        .shell {
            width: min(1120px, 100%);
            margin: 0 auto;
            padding: 18px 14px max(26px, env(safe-area-inset-bottom));
            position: relative;
        }

        .hero {
            position: relative;
            overflow: hidden;
            border: 1px solid var(--line);
            border-radius: var(--radius-xl);
            background:
                linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.03)),
                rgba(14, 18, 34, .78);
            box-shadow: var(--shadow);
            padding: 20px;
            isolation: isolate;
        }

        .hero::before {
            content: "";
            position: absolute;
            inset: -1px;
            z-index: -2;
            background:
                linear-gradient(120deg, rgba(82,232,255,.22), transparent 28%, rgba(156,124,255,.18) 62%, rgba(255,95,183,.18));
        }

        .hero::after {
            content: "";
            position: absolute;
            right: -90px;
            top: -120px;
            width: 270px;
            height: 270px;
            border-radius: 999px;
            background: radial-gradient(circle, rgba(82,232,255,.32), transparent 68%);
            filter: blur(8px);
            z-index: -1;
        }

        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            margin-bottom: 28px;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 0;
        }

        .brand-mark {
            width: 44px;
            height: 44px;
            display: grid;
            place-items: center;
            border-radius: 16px;
            background:
                linear-gradient(135deg, rgba(82,232,255,.95), rgba(156,124,255,.88) 54%, rgba(255,95,183,.9));
            color: #07101c;
            font-weight: 950;
            letter-spacing: -.08em;
            box-shadow: 0 12px 40px rgba(82,232,255,.20);
        }

        .brand-copy strong {
            display: block;
            font-size: 1rem;
            letter-spacing: -.03em;
        }

        .brand-copy span {
            display: block;
            color: var(--muted);
            font-size: .78rem;
            margin-top: 1px;
        }

        .chips {
            display: none;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 8px;
        }

        .chip {
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 8px 11px;
            color: rgba(246,247,251,.86);
            background: rgba(255,255,255,.055);
            font-size: .75rem;
            font-weight: 750;
        }

        .hero-grid {
            display: grid;
            gap: 20px;
        }

        .headline {
            margin: 0;
            font-size: clamp(2.35rem, 13vw, 5.8rem);
            line-height: .88;
            letter-spacing: -.08em;
            max-width: 780px;
        }

        .headline span {
            display: block;
            background: linear-gradient(90deg, var(--text), #e9fbff 40%, #b9b0ff 72%, #ffb8df);
            -webkit-background-clip: text;
            background-clip: text;
            color: transparent;
        }

        .lede {
            margin: 16px 0 0;
            color: var(--muted);
            font-size: 1rem;
            line-height: 1.65;
            max-width: 650px;
        }

        .target-card {
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            background: rgba(5, 8, 18, .45);
            padding: 16px;
        }

        .eyebrow {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 0 0 9px;
            color: var(--cyan);
            font-size: .72rem;
            font-weight: 850;
            letter-spacing: .16em;
            text-transform: uppercase;
        }

        .eyebrow::before {
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: currentColor;
            box-shadow: 0 0 20px currentColor;
        }

        .target-id {
            margin: 0;
            overflow-wrap: anywhere;
            font-size: 1.02rem;
            font-weight: 850;
            letter-spacing: -.03em;
        }

        .target-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 14px;
        }

        .meta-pill {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            min-height: 32px;
            border-radius: 999px;
            border: 1px solid var(--line);
            padding: 7px 10px;
            background: rgba(255,255,255,.05);
            color: rgba(246,247,251,.82);
            font-size: .78rem;
            font-weight: 750;
        }

        .flow {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 8px;
            margin-top: 14px;
        }

        .flow-step {
            min-height: 58px;
            border: 1px solid var(--line);
            border-radius: var(--radius-sm);
            background: rgba(255,255,255,.045);
            padding: 10px;
        }

        .flow-step small {
            display: block;
            color: var(--muted-2);
            font-size: .62rem;
            font-weight: 850;
            text-transform: uppercase;
            letter-spacing: .11em;
        }

        .flow-step strong {
            display: block;
            margin-top: 5px;
            font-size: .78rem;
            letter-spacing: -.02em;
        }

        .panel {
            margin-top: 14px;
            border: 1px solid var(--line);
            border-radius: var(--radius-xl);
            background: var(--panel);
            box-shadow: var(--shadow);
            overflow: hidden;
            backdrop-filter: blur(22px);
        }

        .panel-inner {
            padding: 18px;
        }

        .sync-form {
            display: grid;
            gap: 18px;
        }

        .section {
            border: 1px solid var(--line);
            border-radius: var(--radius-lg);
            background: rgba(255,255,255,.045);
            padding: 15px;
        }

        .section-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 13px;
        }

        .section-kicker {
            color: var(--muted-2);
            font-size: .69rem;
            font-weight: 900;
            letter-spacing: .15em;
            text-transform: uppercase;
        }

        .section h2 {
            margin: 4px 0 0;
            font-size: 1.04rem;
            letter-spacing: -.04em;
        }

        .section p {
            margin: 5px 0 0;
            color: var(--muted);
            font-size: .86rem;
            line-height: 1.5;
        }

        .step-number {
            flex: 0 0 auto;
            display: grid;
            place-items: center;
            width: 36px;
            height: 36px;
            border: 1px solid var(--line-strong);
            border-radius: 13px;
            color: var(--cyan);
            background: rgba(82,232,255,.08);
            font-weight: 950;
        }

        .provider-grid,
        .status-grid {
            display: grid;
            gap: 10px;
        }

        .provider-card,
        .status-card {
            position: relative;
            display: flex;
            align-items: center;
            gap: 12px;
            min-height: 66px;
            border: 1px solid var(--line);
            border-radius: var(--radius-md);
            background: rgba(7, 10, 22, .52);
            padding: 12px;
            cursor: pointer;
            transition: transform .18s ease, border-color .18s ease, background .18s ease, box-shadow .18s ease;
            overflow: hidden;
        }

        .provider-card:active,
        .status-card:active,
        .score-pill:active,
        .stepper button:active,
        .submit-button:active {
            transform: translateY(1px) scale(.995);
        }

        .provider-card:hover,
        .status-card:hover {
            border-color: rgba(82,232,255,.38);
            background: rgba(255,255,255,.07);
        }

        .provider-card input,
        .status-card input,
        .score-pill input {
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }

        .provider-check,
        .status-card__icon {
            flex: 0 0 auto;
            display: grid;
            place-items: center;
            width: 40px;
            height: 40px;
            border-radius: 14px;
            border: 1px solid var(--line-strong);
            color: var(--muted);
            background: rgba(255,255,255,.045);
            font-weight: 950;
        }

        .provider-card input:checked ~ .provider-check,
        .status-card input:checked ~ .status-card__icon {
            color: #07101c;
            border-color: transparent;
            background: linear-gradient(135deg, var(--cyan), var(--violet));
            box-shadow: 0 0 28px rgba(82,232,255,.18);
        }

        .provider-card input:checked ~ div strong,
        .status-card input:checked ~ span strong {
            color: var(--text);
        }

        .provider-card:has(input:checked),
        .status-card:has(input:checked) {
            border-color: rgba(82,232,255,.55);
            box-shadow: inset 0 0 0 1px rgba(82,232,255,.13), 0 18px 42px rgba(0,0,0,.22);
        }

        .provider-card strong,
        .status-card strong {
            display: block;
            color: rgba(246,247,251,.88);
            font-size: .94rem;
            letter-spacing: -.025em;
        }

        .provider-card small,
        .status-card small {
            display: block;
            margin-top: 3px;
            color: var(--muted);
            font-size: .78rem;
        }

        .provider-card.is-disabled {
            cursor: not-allowed;
            opacity: .48;
        }

        .status-card__glow {
            position: absolute;
            inset: auto 16px -32px auto;
            width: 90px;
            height: 90px;
            border-radius: 999px;
            opacity: .24;
            filter: blur(22px);
            background: var(--cyan);
            pointer-events: none;
        }

        .status-card--violet .status-card__glow { background: var(--violet); }
        .status-card--green .status-card__glow { background: var(--green); }
        .status-card--amber .status-card__glow { background: var(--amber); }
        .status-card--red .status-card__glow { background: var(--red); }

        .control-grid {
            display: grid;
            gap: 14px;
        }

        .field-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 9px;
            color: var(--muted);
            font-size: .78rem;
            font-weight: 850;
            letter-spacing: .08em;
            text-transform: uppercase;
        }

        .progress-readout,
        .score-readout {
            color: var(--text);
            font-size: .9rem;
            letter-spacing: -.02em;
            text-transform: none;
        }

        .stepper {
            display: grid;
            grid-template-columns: var(--tap) minmax(0, 1fr) var(--tap);
            gap: 9px;
        }

        .stepper button,
        .stepper input {
            min-height: var(--tap);
            border: 1px solid var(--line);
            border-radius: var(--radius-sm);
            color: var(--text);
            background: rgba(7, 10, 22, .62);
            outline: none;
        }

        .stepper button {
            cursor: pointer;
            font-size: 1.3rem;
            font-weight: 900;
        }

        .stepper button:hover {
            border-color: rgba(82,232,255,.45);
            background: rgba(82,232,255,.08);
        }

        .stepper input {
            width: 100%;
            text-align: center;
            font-weight: 950;
            font-size: 1.25rem;
            letter-spacing: -.04em;
        }

        .stepper input:focus,
        .score-pill:has(input:focus-visible),
        .provider-card:has(input:focus-visible),
        .status-card:has(input:focus-visible) {
            border-color: rgba(82,232,255,.8);
            box-shadow: 0 0 0 4px rgba(82,232,255,.12);
        }

        .score-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 8px;
        }

        .score-pill {
            position: relative;
            min-height: 44px;
            display: grid;
            place-items: center;
            border: 1px solid var(--line);
            border-radius: 999px;
            color: var(--muted);
            background: rgba(7, 10, 22, .54);
            cursor: pointer;
            transition: border-color .18s ease, background .18s ease, color .18s ease, transform .18s ease;
        }

        .score-pill:first-child {
            grid-column: span 2;
        }

        .score-pill span {
            font-size: .84rem;
            font-weight: 850;
        }

        .score-pill:has(input:checked) {
            color: #07101c;
            border-color: transparent;
            background: linear-gradient(135deg, var(--amber), var(--pink));
            box-shadow: 0 12px 36px rgba(255,209,102,.16);
        }

        .submit-zone {
            display: grid;
            gap: 12px;
        }

        .submit-button {
            width: 100%;
            min-height: 58px;
            border: 0;
            border-radius: 20px;
            color: #050711;
            background:
                linear-gradient(135deg, var(--cyan), var(--violet) 58%, var(--pink));
            font-weight: 950;
            letter-spacing: -.035em;
            cursor: pointer;
            box-shadow: 0 22px 55px rgba(82,232,255,.22), inset 0 1px 0 rgba(255,255,255,.45);
            transition: transform .18s ease, filter .18s ease;
        }

        .submit-button:hover { filter: brightness(1.07); }
        .submit-button:disabled { cursor: progress; filter: grayscale(.25) brightness(.75); }

        .status-message {
            min-height: 44px;
            display: none;
            align-items: center;
            border-radius: var(--radius-sm);
            border: 1px solid var(--line);
            padding: 11px 13px;
            background: rgba(255,255,255,.045);
            color: var(--muted);
            font-size: .88rem;
            line-height: 1.35;
        }

        .status-message.is-visible { display: flex; }
        .status-message.is-success { border-color: rgba(122,240,180,.38); color: var(--green); background: rgba(122,240,180,.08); }
        .status-message.is-error { border-color: rgba(255,107,122,.38); color: var(--red); background: rgba(255,107,122,.08); }

        details {
            border-top: 1px solid var(--line);
            background: rgba(0,0,0,.13);
        }

        summary {
            min-height: 54px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            cursor: pointer;
            padding: 0 18px;
            color: var(--muted);
            font-size: .82rem;
            font-weight: 850;
            letter-spacing: .08em;
            text-transform: uppercase;
        }

        .details-grid {
            display: grid;
            gap: 9px;
            padding: 0 18px 18px;
        }

        .detail-row {
            display: grid;
            grid-template-columns: 88px minmax(0, 1fr);
            gap: 12px;
            align-items: center;
            min-height: 42px;
            border: 1px solid var(--line);
            border-radius: var(--radius-sm);
            padding: 10px 12px;
            background: rgba(255,255,255,.035);
        }

        .detail-row dt {
            color: var(--muted-2);
            font-size: .7rem;
            font-weight: 900;
            letter-spacing: .12em;
            text-transform: uppercase;
        }

        .detail-row dd {
            margin: 0;
            overflow-wrap: anywhere;
            color: rgba(246,247,251,.88);
            font-size: .84rem;
            font-weight: 750;
        }

        @media (min-width: 680px) {
            .shell { padding: 28px; }
            .hero { padding: 28px; }
            .chips { display: flex; }
            .hero-grid { grid-template-columns: minmax(0, 1.35fr) minmax(300px, .65fr); align-items: end; }
            .panel-inner { padding: 22px; }
            .provider-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .status-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
            .status-card { align-items: flex-start; min-height: 132px; flex-direction: column; justify-content: space-between; }
            .control-grid { grid-template-columns: minmax(0, .85fr) minmax(0, 1.15fr); align-items: start; }
            .score-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
            .submit-zone { grid-template-columns: minmax(0, 1fr) 220px; align-items: center; }
            .submit-button { order: 2; }
            .status-message { order: 1; }
        }

        @media (min-width: 980px) {
            .shell { padding-top: 36px; }
            .panel { margin-top: -18px; position: relative; z-index: 2; }
            .sync-form { grid-template-columns: .72fr 1.28fr; align-items: start; }
            .section--providers { position: sticky; top: 18px; }
            .section--status,
            .section--controls,
            .submit-zone { grid-column: 2; }
        }

        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                scroll-behavior: auto !important;
                transition: none !important;
            }
        }
    </style>
</head>
<body>
    <main class="shell">
        <section class="hero" aria-labelledby="page-title">
            <div class="topbar">
                <div class="brand">
                    <div class="brand-mark" aria-hidden="true">AS</div>
                    <div class="brand-copy">
                        <strong>AniSync</strong>
                        <span>Stremio control panel</span>
                    </div>
                </div>
                <div class="chips" aria-label="Supported services">
                    <span class="chip">MyAnimeList</span>
                    <span class="chip">AniList</span>
                    <span class="chip">Stremio</span>
                </div>
            </div>

            <div class="hero-grid">
                <div>
                    <p class="eyebrow">Manual sync panel</p>
                    <h1 id="page-title" class="headline"><span>Update anime</span><span>in one move.</span></h1>
                    <p class="lede">Set status, episode progress, and score from the same page Stremio opens. Only selected trackers are updated.</p>
                </div>

                <aside class="target-card" aria-label="Current target">
                    <p class="eyebrow">Current target</p>
                    <p class="target-id">__SAFE_META_ID__</p>
                    <div class="target-meta">
                        <span class="meta-pill">Type · __SAFE_TYPE__</span>
                        <span class="meta-pill">Progress · <span data-progress-mirror>__DEFAULT_PROGRESS__</span></span>
                    </div>
                    <div class="flow" aria-label="Sync flow">
                        <div class="flow-step"><small>01</small><strong>Stremio</strong></div>
                        <div class="flow-step"><small>02</small><strong>AniSync</strong></div>
                        <div class="flow-step"><small>03</small><strong>MAL / AniList</strong></div>
                    </div>
                </aside>
            </div>
        </section>

        <section class="panel" aria-label="Update form">
            <div class="panel-inner">
                <form class="sync-form" id="sync-form" method="post" action="__FORM_ACTION__">
                    <input type="hidden" name="meta_id" value="__SAFE_META_ID__" />

                    <section class="section section--providers">
                        <div class="section-head">
                            <div>
                                <div class="section-kicker">Step 1</div>
                                <h2>Choose tracker</h2>
                                <p>Unavailable trackers are locked automatically.</p>
                            </div>
                            <div class="step-number">1</div>
                        </div>

                        <div class="provider-grid">
                            <label class="provider-card __MAL_STATE__" for="provider_mal">
                                <input id="provider_mal" type="checkbox" name="provider_mal" value="1" __MAL_CHECKED__ __MAL_DISABLED__ />
                                <span class="provider-check">M</span>
                                <div>
                                    <strong>MyAnimeList</strong>
                                    <small>__MAL_NOTE__</small>
                                </div>
                            </label>

                            <label class="provider-card __ANILIST_STATE__" for="provider_anilist">
                                <input id="provider_anilist" type="checkbox" name="provider_anilist" value="1" __ANILIST_CHECKED__ __ANILIST_DISABLED__ />
                                <span class="provider-check">A</span>
                                <div>
                                    <strong>AniList</strong>
                                    <small>__ANILIST_NOTE__</small>
                                </div>
                            </label>
                        </div>
                    </section>

                    <section class="section section--status">
                        <div class="section-head">
                            <div>
                                <div class="section-kicker">Step 2</div>
                                <h2>Set anime state</h2>
                                <p>Pick exactly one status for your list entry.</p>
                            </div>
                            <div class="step-number">2</div>
                        </div>

                        <div class="status-grid" role="radiogroup" aria-label="Status">
__STATUS_CARDS__
                        </div>
                    </section>

                    <section class="section section--controls">
                        <div class="section-head">
                            <div>
                                <div class="section-kicker">Step 3</div>
                                <h2>Progress and score</h2>
                                <p>Use episode progress from Stremio or adjust it manually.</p>
                            </div>
                            <div class="step-number">3</div>
                        </div>

                        <div class="control-grid">
                            <div>
                                <label class="field-label" for="progress">
                                    Episode count
                                    <span class="progress-readout"><span data-progress-mirror>__DEFAULT_PROGRESS__</span> eps</span>
                                </label>
                                <div class="stepper">
                                    <button type="button" data-progress-step="-1" aria-label="Decrease progress">−</button>
                                    <input id="progress" name="progress" type="number" min="0" inputmode="numeric" value="__DEFAULT_PROGRESS__" />
                                    <button type="button" data-progress-step="1" aria-label="Increase progress">+</button>
                                </div>
                            </div>

                            <div>
                                <div class="field-label">
                                    Your score
                                    <span class="score-readout"><span id="score-readout">—</span> / 10</span>
                                </div>
                                <div class="score-grid" role="radiogroup" aria-label="Score">
__SCORE_CONTROLS__
                                </div>
                            </div>
                        </div>
                    </section>

                    <div class="submit-zone">
                        <button class="submit-button" id="submit-button" type="submit">Sync Progress</button>
                        <div class="status-message" id="status-message" role="status" aria-live="polite"></div>
                    </div>
                </form>
            </div>

            <details>
                <summary>Technical details</summary>
                <dl class="details-grid">
                    <div class="detail-row"><dt>ID</dt><dd>__SAFE_META_ID__</dd></div>
                    <div class="detail-row"><dt>Type</dt><dd>__SAFE_TYPE__</dd></div>
                    <div class="detail-row"><dt>Kitsu</dt><dd>__KITSU_ID__</dd></div>
                    <div class="detail-row"><dt>MAL</dt><dd>__MAL_ID__</dd></div>
                    <div class="detail-row"><dt>AniList</dt><dd>__ANILIST_ID__</dd></div>
                    <div class="detail-row"><dt>Simkl</dt><dd>__SIMKL_ID__</dd></div>
                </dl>
            </details>
        </section>
    </main>

    <script>
        (() => {
            const form = document.getElementById('sync-form');
            const progress = document.getElementById('progress');
            const progressMirrors = document.querySelectorAll('[data-progress-mirror]');
            const scoreReadout = document.getElementById('score-readout');
            const submitButton = document.getElementById('submit-button');
            const statusMessage = document.getElementById('status-message');

            const clampProgress = () => {
                const parsed = Number.parseInt(progress.value || '0', 10);
                const next = Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
                progress.value = String(next);
                progressMirrors.forEach((node) => { node.textContent = String(next); });
            };

            document.querySelectorAll('[data-progress-step]').forEach((button) => {
                button.addEventListener('click', () => {
                    const step = Number.parseInt(button.dataset.progressStep || '0', 10);
                    const current = Number.parseInt(progress.value || '0', 10) || 0;
                    progress.value = String(Math.max(0, current + step));
                    clampProgress();
                });
            });

            progress.addEventListener('input', clampProgress);
            clampProgress();

            document.querySelectorAll('input[name="score"]').forEach((input) => {
                input.addEventListener('change', () => {
                    scoreReadout.textContent = input.value || '—';
                });
            });

            const showMessage = (kind, text) => {
                statusMessage.textContent = text;
                statusMessage.className = `status-message is-visible is-${kind}`;
            };

            form.addEventListener('submit', async (event) => {
                event.preventDefault();
                clampProgress();

                submitButton.disabled = true;
                submitButton.textContent = 'Syncing…';
                showMessage('success', 'Sending update to selected trackers…');

                try {
                    const response = await fetch(form.action, {
                        method: 'POST',
                        body: new FormData(form),
                        headers: { 'Accept': 'application/json' },
                    });

                    const payload = await response.json().catch(() => ({}));
                    const message = payload.message || (response.ok ? 'Updated successfully.' : 'Update failed.');
                    showMessage(response.ok ? 'success' : 'error', message);
                } catch (error) {
                    showMessage('error', 'Network error. Open AniSync again and retry.');
                } finally {
                    submitButton.disabled = false;
                    submitButton.textContent = 'Sync Progress';
                }
            });
        })();
    </script>
</body>
</html>"""

    return (
        page
        .replace('__FORM_ACTION__', safe_form_action)
        .replace('__SAFE_META_ID__', safe_meta_id)
        .replace('__SAFE_TYPE__', safe_type)
        .replace('__DEFAULT_PROGRESS__', safe_default_progress)
        .replace('__KITSU_ID__', safe_kitsu_id)
        .replace('__MAL_ID__', safe_mal_id)
        .replace('__ANILIST_ID__', safe_anilist_id)
        .replace('__SIMKL_ID__', safe_simkl_id)
        .replace('__MAL_NOTE__', mal_note)
        .replace('__ANILIST_NOTE__', anilist_note)
        .replace('__MAL_CHECKED__', mal_checked)
        .replace('__ANILIST_CHECKED__', anilist_checked)
        .replace('__MAL_DISABLED__', mal_disabled)
        .replace('__ANILIST_DISABLED__', anilist_disabled)
        .replace('__MAL_STATE__', mal_state)
        .replace('__ANILIST_STATE__', anilist_state)
        .replace('__STATUS_CARDS__', status_cards)
        .replace('__SCORE_CONTROLS__', score_controls)
    )



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