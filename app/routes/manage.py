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
    mal_state = "" if mal_available else "is-disabled"
    anilist_state = "" if anilist_available else "is-disabled"

    mal_note = html.escape(provider_note(mal_has_token, mal_enabled, mal_has_id))
    anilist_note = html.escape(provider_note(anilist_has_token, anilist_enabled, anilist_has_id))

    status_meta = {
        "watching": {
            "hint": "Currently active",
            "icon": "▶",
            "tone": "cyan",
        },
        "plan_to_watch": {
            "hint": "Queue for later",
            "icon": "＋",
            "tone": "violet",
        },
        "completed": {
            "hint": "Finished",
            "icon": "✓",
            "tone": "green",
        },
        "on_hold": {
            "hint": "Paused",
            "icon": "Ⅱ",
            "tone": "amber",
        },
        "dropped": {
            "hint": "Stopped",
            "icon": "×",
            "tone": "red",
        },
    }

    status_cards = ""
    for key, data in STATUS_MAP.items():
        meta = status_meta.get(key, {})
        checked = "checked" if key == "watching" else ""
        status_cards += f"""
            <label class="status-card tone-{html.escape(meta.get("tone", "cyan"))}">
                <input class="status-input" type="radio" name="status" value="{html.escape(key, quote=True)}" {checked}>
                <span class="status-shell">
                    <span class="status-icon" aria-hidden="true">{html.escape(meta.get("icon", "•"))}</span>
                    <span>
                        <strong>{html.escape(data["label"])}</strong>
                        <small>{html.escape(meta.get("hint", ""))}</small>
                    </span>
                </span>
            </label>
        """

    score_controls = """
        <label class="score-clear">
            <input class="score-input" type="radio" name="score" value="" checked>
            <span>No score</span>
        </label>
    """
    for value in range(0, 11):
        score_controls += f"""
            <label class="score-choice" aria-label="Score {value} out of 10">
                <input class="score-input" type="radio" name="score" value="{value}">
                <span class="score-mark">{value}</span>
            </label>
        """

    return f"""
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AniSync · Manage Progress</title>
    <style>
        :root {{
            --bg: #070914;
            --bg-2: #0b1020;
            --surface: rgba(15, 22, 40, .86);
            --surface-2: rgba(255, 255, 255, .055);
            --surface-3: rgba(255, 255, 255, .085);
            --line: rgba(255, 255, 255, .12);
            --line-strong: rgba(255, 255, 255, .22);
            --text: #f5f7fb;
            --muted: #a3adbf;
            --muted-2: #687386;
            --cyan: #30dfff;
            --cyan-dark: #0898bc;
            --violet: #a78bfa;
            --amber: #ffc46b;
            --green: #54f0a7;
            --red: #ff6b8a;
            --shadow: 0 24px 80px rgba(0, 0, 0, .48);
            --radius: 28px;
            --radius-sm: 18px;
        }}

        * {{
            box-sizing: border-box;
        }}

        html {{
            min-height: 100%;
            background: var(--bg);
        }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at 12% 8%, rgba(48, 223, 255, .18), transparent 32%),
                radial-gradient(circle at 82% 18%, rgba(167, 139, 250, .17), transparent 30%),
                radial-gradient(circle at 52% 96%, rgba(255, 196, 107, .09), transparent 34%),
                linear-gradient(180deg, #050712 0%, #0b1020 48%, #070914 100%);
            overflow-x: hidden;
        }}

        body::before {{
            content: "";
            position: fixed;
            inset: -20%;
            pointer-events: none;
            background:
                linear-gradient(115deg, transparent 0 42%, rgba(48, 223, 255, .055) 42% 43%, transparent 43% 100%),
                linear-gradient(245deg, transparent 0 58%, rgba(167, 139, 250, .05) 58% 59%, transparent 59% 100%);
            mask-image: radial-gradient(circle at center, black, transparent 72%);
            animation: drift 18s ease-in-out infinite alternate;
        }}

        @keyframes drift {{
            from {{
                transform: translate3d(-1%, -1%, 0) rotate(-1deg);
            }}
            to {{
                transform: translate3d(1%, 1%, 0) rotate(1deg);
            }}
        }}

        a {{
            color: inherit;
        }}

        button,
        input,
        summary {{
            font: inherit;
        }}

        button {{
            -webkit-tap-highlight-color: transparent;
        }}

        .page {{
            position: relative;
            width: min(100%, 1080px);
            margin: 0 auto;
            padding: 22px;
        }}

        .topbar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            margin: 8px 0 22px;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 0;
        }}

        .brand-mark {{
            width: 42px;
            height: 42px;
            display: grid;
            place-items: center;
            border-radius: 14px;
            background:
                linear-gradient(135deg, rgba(48, 223, 255, .28), rgba(167, 139, 250, .18)),
                rgba(255, 255, 255, .06);
            border: 1px solid rgba(255, 255, 255, .16);
            box-shadow: 0 0 36px rgba(48, 223, 255, .12);
            font-weight: 950;
            letter-spacing: -.06em;
        }}

        .brand h1 {{
            margin: 0;
            font-size: 18px;
            letter-spacing: -.03em;
        }}

        .brand p {{
            margin: 2px 0 0;
            color: var(--muted);
            font-size: 13px;
        }}

        .chips {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }}

        .chip {{
            padding: 7px 10px;
            border: 1px solid var(--line);
            border-radius: 999px;
            color: #dbe4f4;
            background: rgba(255, 255, 255, .045);
            font-size: 12px;
            font-weight: 750;
        }}

        .hero {{
            position: relative;
            border-radius: var(--radius);
            background:
                linear-gradient(145deg, rgba(255, 255, 255, .09), rgba(255, 255, 255, .035)),
                var(--surface);
            border: 1px solid rgba(255, 255, 255, .14);
            box-shadow: var(--shadow);
            overflow: hidden;
        }}

        .hero::before {{
            content: "";
            position: absolute;
            width: 360px;
            height: 360px;
            right: -170px;
            top: -190px;
            border-radius: 50%;
            border: 1px solid rgba(48, 223, 255, .2);
            box-shadow:
                inset 0 0 48px rgba(48, 223, 255, .05),
                0 0 92px rgba(167, 139, 250, .09);
            animation: orbit 12s linear infinite;
        }}

        .hero::after {{
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            background:
                linear-gradient(90deg, rgba(255, 255, 255, .08), transparent 18% 82%, rgba(255, 255, 255, .035)),
                radial-gradient(circle at 78% 8%, rgba(48, 223, 255, .15), transparent 28%);
        }}

        @keyframes orbit {{
            to {{
                transform: rotate(360deg);
            }}
        }}

        .hero-inner {{
            position: relative;
            z-index: 1;
            padding: clamp(22px, 5vw, 48px);
        }}

        .hero-head {{
            display: grid;
            grid-template-columns: 1.2fr .8fr;
            gap: 26px;
            align-items: start;
            margin-bottom: 28px;
        }}

        .eyebrow {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            width: fit-content;
            margin-bottom: 14px;
            padding: 7px 10px;
            border-radius: 999px;
            background: rgba(48, 223, 255, .09);
            border: 1px solid rgba(48, 223, 255, .22);
            color: #bff6ff;
            font-size: 12px;
            font-weight: 850;
            text-transform: uppercase;
            letter-spacing: .09em;
        }}

        .eyebrow::before {{
            content: "";
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--cyan);
            box-shadow: 0 0 18px var(--cyan);
        }}

        .hero-title {{
            margin: 0;
            max-width: 720px;
            font-size: clamp(36px, 7vw, 74px);
            line-height: .92;
            letter-spacing: -.07em;
            font-weight: 950;
        }}

        .hero-copy {{
            margin: 16px 0 0;
            max-width: 620px;
            color: var(--muted);
            font-size: 15px;
            line-height: 1.65;
        }}

        .target-card {{
            align-self: stretch;
            padding: 18px;
            border-radius: 22px;
            background: rgba(0, 0, 0, .22);
            border: 1px solid var(--line);
        }}

        .target-label {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: .1em;
        }}

        .target-id {{
            margin-top: 14px;
            padding: 14px;
            border-radius: 16px;
            background: rgba(255, 255, 255, .045);
            border: 1px solid rgba(255, 255, 255, .08);
            color: #f7fbff;
            font-size: 14px;
            line-height: 1.45;
            word-break: break-all;
        }}

        .target-meta {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 12px;
        }}

        .mini {{
            padding: 7px 9px;
            border-radius: 10px;
            background: rgba(255, 255, 255, .06);
            color: #c8d2e3;
            font-size: 12px;
            font-weight: 750;
        }}

        .flow {{
            display: grid;
            grid-template-columns: 1fr auto 1fr auto 1fr;
            gap: 10px;
            align-items: center;
            margin: 8px 0 28px;
            color: #dce8ff;
        }}

        .flow-node {{
            min-width: 0;
            padding: 12px 14px;
            border-radius: 16px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .045);
            text-align: center;
            font-size: 13px;
            font-weight: 850;
        }}

        .flow-arrow {{
            width: 34px;
            height: 1px;
            background: linear-gradient(90deg, var(--cyan), var(--violet));
            position: relative;
            opacity: .85;
        }}

        .flow-arrow::after {{
            content: "";
            position: absolute;
            right: -1px;
            top: 50%;
            width: 7px;
            height: 7px;
            border-top: 1px solid var(--violet);
            border-right: 1px solid var(--violet);
            transform: translateY(-50%) rotate(45deg);
        }}

        .form-grid {{
            display: grid;
            gap: 18px;
        }}

        .panel {{
            padding: 20px;
            border-radius: 24px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .045);
        }}

        .panel-head {{
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 14px;
            margin-bottom: 14px;
        }}

        .step {{
            margin: 0;
            color: var(--cyan);
            font-size: 12px;
            font-weight: 950;
            letter-spacing: .11em;
            text-transform: uppercase;
        }}

        .panel-title {{
            margin: 4px 0 0;
            font-size: 18px;
            letter-spacing: -.03em;
        }}

        .panel-hint {{
            margin: 0;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.45;
        }}

        .providers {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }}

        .provider-card {{
            display: block;
            cursor: pointer;
        }}

        .provider-input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .provider-shell {{
            display: flex;
            justify-content: space-between;
            gap: 14px;
            min-height: 92px;
            padding: 16px;
            border-radius: 20px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .045);
            transition: transform .16s ease, border-color .16s ease, background .16s ease, box-shadow .16s ease;
        }}

        .provider-card:not(.is-disabled):hover .provider-shell {{
            transform: translateY(-1px);
            border-color: rgba(48, 223, 255, .34);
            background: rgba(255, 255, 255, .07);
        }}

        .provider-input:focus-visible + .provider-shell,
        .status-input:focus-visible + .status-shell,
        .score-input:focus-visible + .score-mark,
        .score-clear .score-input:focus-visible + span,
        .round-btn:focus-visible,
        .sync-button:focus-visible,
        summary:focus-visible {{
            outline: 3px solid rgba(48, 223, 255, .36);
            outline-offset: 3px;
        }}

        .provider-input:checked + .provider-shell {{
            border-color: rgba(48, 223, 255, .58);
            background:
                linear-gradient(135deg, rgba(48, 223, 255, .14), rgba(167, 139, 250, .08)),
                rgba(255, 255, 255, .055);
            box-shadow: 0 0 42px rgba(48, 223, 255, .08);
        }}

        .provider-input:disabled + .provider-shell {{
            opacity: .46;
            cursor: not-allowed;
            filter: grayscale(.2);
        }}

        .provider-name {{
            display: block;
            font-weight: 900;
            letter-spacing: -.02em;
        }}

        .provider-note {{
            display: block;
            margin-top: 7px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 750;
        }}

        .provider-toggle {{
            width: 42px;
            height: 24px;
            flex: 0 0 auto;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .08);
            position: relative;
            margin-top: 1px;
        }}

        .provider-toggle::after {{
            content: "";
            position: absolute;
            left: 3px;
            top: 3px;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: var(--muted);
            transition: transform .16s ease, background .16s ease;
        }}

        .provider-input:checked + .provider-shell .provider-toggle::after {{
            transform: translateX(18px);
            background: var(--cyan);
            box-shadow: 0 0 18px rgba(48, 223, 255, .7);
        }}

        .status-grid {{
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 10px;
        }}

        .status-card {{
            display: block;
            min-width: 0;
            cursor: pointer;
        }}

        .status-input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .status-shell {{
            display: grid;
            gap: 10px;
            min-height: 120px;
            padding: 14px;
            border-radius: 20px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .04);
            transition: transform .16s ease, border-color .16s ease, background .16s ease;
        }}

        .status-card:hover .status-shell {{
            transform: translateY(-1px);
            background: rgba(255, 255, 255, .07);
        }}

        .status-icon {{
            width: 34px;
            height: 34px;
            display: grid;
            place-items: center;
            border-radius: 13px;
            background: rgba(255, 255, 255, .08);
            border: 1px solid rgba(255, 255, 255, .1);
            font-weight: 950;
        }}

        .status-shell strong {{
            display: block;
            font-size: 14px;
            line-height: 1.15;
            letter-spacing: -.02em;
        }}

        .status-shell small {{
            display: block;
            margin-top: 5px;
            color: var(--muted);
            font-size: 11px;
            line-height: 1.25;
        }}

        .status-input:checked + .status-shell {{
            background:
                linear-gradient(135deg, rgba(48, 223, 255, .14), rgba(255, 255, 255, .05)),
                rgba(255, 255, 255, .055);
            border-color: rgba(48, 223, 255, .55);
        }}

        .tone-violet .status-input:checked + .status-shell {{
            border-color: rgba(167, 139, 250, .65);
            background: linear-gradient(135deg, rgba(167, 139, 250, .16), rgba(255, 255, 255, .05));
        }}

        .tone-green .status-input:checked + .status-shell {{
            border-color: rgba(84, 240, 167, .62);
            background: linear-gradient(135deg, rgba(84, 240, 167, .14), rgba(255, 255, 255, .05));
        }}

        .tone-amber .status-input:checked + .status-shell {{
            border-color: rgba(255, 196, 107, .62);
            background: linear-gradient(135deg, rgba(255, 196, 107, .14), rgba(255, 255, 255, .05));
        }}

        .tone-red .status-input:checked + .status-shell {{
            border-color: rgba(255, 107, 138, .62);
            background: linear-gradient(135deg, rgba(255, 107, 138, .14), rgba(255, 255, 255, .05));
        }}

        .control-row {{
            display: grid;
            grid-template-columns: minmax(0, .72fr) minmax(0, 1fr);
            gap: 14px;
            align-items: stretch;
        }}

        .progress-box {{
            display: grid;
            grid-template-columns: 52px minmax(84px, 1fr) 52px;
            gap: 10px;
            align-items: center;
            padding: 10px;
            border-radius: 22px;
            border: 1px solid var(--line);
            background: rgba(0, 0, 0, .18);
        }}

        .round-btn {{
            width: 52px;
            height: 52px;
            border: 0;
            border-radius: 17px;
            color: var(--text);
            background: rgba(255, 255, 255, .08);
            cursor: pointer;
            font-size: 24px;
            font-weight: 900;
            transition: transform .12s ease, background .12s ease;
        }}

        .round-btn:hover {{
            background: rgba(255, 255, 255, .13);
        }}

        .round-btn:active {{
            transform: scale(.94);
        }}

        .progress-num {{
            width: 100%;
            min-width: 0;
            height: 52px;
            border: 0;
            border-radius: 17px;
            outline: none;
            color: var(--text);
            background: rgba(255, 255, 255, .065);
            text-align: center;
            font-size: 28px;
            font-weight: 950;
            letter-spacing: -.04em;
            appearance: textfield;
        }}

        .progress-num.pulse {{
            animation: pulse .2s ease;
        }}

        @keyframes pulse {{
            50% {{
                transform: scale(1.035);
            }}
        }}

        .progress-num::-webkit-outer-spin-button,
        .progress-num::-webkit-inner-spin-button {{
            -webkit-appearance: none;
            margin: 0;
        }}

        .score-wrap {{
            display: grid;
            gap: 12px;
        }}

        .score-top {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
        }}

        .score-display {{
            color: var(--cyan);
            font-size: 24px;
            font-weight: 950;
            letter-spacing: -.04em;
            white-space: nowrap;
        }}

        .score-display small {{
            color: var(--muted);
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0;
        }}

        .score-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            align-items: center;
        }}

        .score-choice,
        .score-clear {{
            display: inline-flex;
            cursor: pointer;
        }}

        .score-input {{
            position: absolute;
            opacity: 0;
            pointer-events: none;
        }}

        .score-mark,
        .score-clear span {{
            min-width: 38px;
            height: 38px;
            display: grid;
            place-items: center;
            border: 1px solid var(--line);
            border-radius: 13px;
            background: rgba(255, 255, 255, .045);
            color: var(--muted);
            font-size: 13px;
            font-weight: 900;
            transition: transform .12s ease, background .12s ease, border-color .12s ease, color .12s ease;
        }}

        .score-clear span {{
            width: auto;
            padding: 0 12px;
        }}

        .score-choice:hover .score-mark,
        .score-clear:hover span {{
            transform: translateY(-1px);
            background: rgba(255, 255, 255, .08);
            color: var(--text);
        }}

        .score-choice.is-filled .score-mark {{
            color: #07101c;
            border-color: rgba(48, 223, 255, .72);
            background: linear-gradient(135deg, var(--cyan), var(--violet));
        }}

        .score-choice.is-selected .score-mark,
        .score-clear .score-input:checked + span {{
            border-color: rgba(255, 196, 107, .72);
            box-shadow: 0 0 0 3px rgba(255, 196, 107, .1);
        }}

        .submit-panel {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            padding: 18px;
            border-radius: 24px;
            border: 1px solid rgba(48, 223, 255, .2);
            background:
                linear-gradient(135deg, rgba(48, 223, 255, .08), rgba(167, 139, 250, .07)),
                rgba(255, 255, 255, .04);
        }}

        .submit-copy strong {{
            display: block;
            font-size: 14px;
        }}

        .submit-copy span {{
            display: block;
            margin-top: 4px;
            color: var(--muted);
            font-size: 13px;
        }}

        .sync-button {{
            min-width: 178px;
            border: 0;
            border-radius: 18px;
            padding: 15px 20px;
            color: #04101c;
            background: linear-gradient(135deg, var(--cyan), #79f1ff 42%, var(--violet));
            font-size: 15px;
            font-weight: 950;
            letter-spacing: -.02em;
            cursor: pointer;
            box-shadow: 0 16px 34px rgba(48, 223, 255, .18);
            transition: transform .14s ease, filter .14s ease, opacity .14s ease;
        }}

        .sync-button:hover {{
            transform: translateY(-1px);
            filter: brightness(1.05);
        }}

        .sync-button:active {{
            transform: translateY(0) scale(.98);
        }}

        .sync-button:disabled {{
            cursor: wait;
            opacity: .72;
        }}

        .result {{
            margin-top: 14px;
            padding: 13px 15px;
            border-radius: 16px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .045);
            color: var(--muted);
            font-size: 13px;
            font-weight: 850;
        }}

        .result[hidden] {{
            display: none;
        }}

        .result.is-success {{
            border-color: rgba(84, 240, 167, .35);
            background: rgba(84, 240, 167, .08);
            color: #b8ffd9;
        }}

        .result.is-error {{
            border-color: rgba(255, 107, 138, .38);
            background: rgba(255, 107, 138, .08);
            color: #ffc2ce;
        }}

        details {{
            margin-top: 16px;
            border-radius: 18px;
            border: 1px solid var(--line);
            background: rgba(255, 255, 255, .035);
            overflow: hidden;
        }}

        summary {{
            cursor: pointer;
            padding: 14px 16px;
            color: #c8d2e3;
            font-size: 13px;
            font-weight: 850;
        }}

        .details-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin: 0;
            padding: 0 16px 16px;
        }}

        .details-grid div {{
            min-width: 0;
            padding: 12px;
            border-radius: 14px;
            background: rgba(0, 0, 0, .18);
            border: 1px solid rgba(255, 255, 255, .065);
        }}

        .details-grid dt {{
            margin-bottom: 6px;
            color: var(--muted-2);
            font-size: 11px;
            font-weight: 950;
            text-transform: uppercase;
            letter-spacing: .1em;
        }}

        .details-grid dd {{
            margin: 0;
            color: #d9e3f4;
            font-size: 13px;
            line-height: 1.35;
            word-break: break-all;
        }}

        .visually-hidden {{
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        }}

        @media (max-width: 820px) {{
            .page {{
                padding: 14px;
            }}

            .topbar {{
                align-items: flex-start;
                flex-direction: column;
            }}

            .chips {{
                justify-content: flex-start;
            }}

            .hero-head,
            .control-row {{
                grid-template-columns: 1fr;
            }}

            .flow {{
                grid-template-columns: 1fr;
            }}

            .flow-arrow {{
                width: 1px;
                height: 24px;
                margin: 0 auto;
                background: linear-gradient(180deg, var(--cyan), var(--violet));
            }}

            .flow-arrow::after {{
                right: auto;
                left: 50%;
                top: auto;
                bottom: -2px;
                transform: translateX(-50%) rotate(135deg);
            }}

            .providers,
            .details-grid {{
                grid-template-columns: 1fr;
            }}

            .status-grid {{
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }}

            .submit-panel {{
                align-items: stretch;
                flex-direction: column;
            }}

            .sync-button {{
                width: 100%;
            }}
        }}

        @media (max-width: 420px) {{
            .page {{
                padding: 10px;
            }}

            .hero-inner {{
                padding: 18px;
            }}

            .hero-title {{
                font-size: 38px;
            }}

            .panel {{
                padding: 15px;
            }}

            .status-grid {{
                gap: 8px;
            }}

            .status-shell {{
                min-height: 110px;
                padding: 12px;
            }}

            .progress-box {{
                grid-template-columns: 48px minmax(72px, 1fr) 48px;
            }}

            .round-btn {{
                width: 48px;
                height: 48px;
            }}

            .progress-num {{
                height: 48px;
            }}
        }}

        @media (prefers-reduced-motion: reduce) {{
            *,
            *::before,
            *::after {{
                animation-duration: .01ms !important;
                animation-iteration-count: 1 !important;
                scroll-behavior: auto !important;
                transition-duration: .01ms !important;
            }}
        }}
    </style>
</head>
<body>
    <main class="page">
        <header class="topbar" aria-label="AniSync header">
            <div class="brand">
                <div class="brand-mark" aria-hidden="true">AS</div>
                <div>
                    <h1>AniSync</h1>
                    <p>Update anime progress without leaving the Stremio flow.</p>
                </div>
            </div>

            <div class="chips" aria-label="Supported services">
                <span class="chip">MyAnimeList</span>
                <span class="chip">AniList</span>
                <span class="chip">Stremio</span>
            </div>
        </header>

        <section class="hero" aria-labelledby="page-title">
            <div class="hero-inner">
                <div class="hero-head">
                    <div>
                        <span class="eyebrow">Manual Sync Panel</span>
                        <h2 class="hero-title" id="page-title">Where are you in this anime?</h2>
                        <p class="hero-copy">
                            Pick a status, set progress, rate it, and sync. AniSync will update only the trackers selected below.
                        </p>
                    </div>

                    <aside class="target-card" aria-label="Current anime metadata">
                        <div class="target-label">
                            <span>Current target</span>
                            <span>{safe_type}</span>
                        </div>
                        <div class="target-id">{safe_meta_id}</div>
                        <div class="target-meta">
                            <span class="mini">Progress: {default_progress}</span>
                            <span class="mini">Type: {safe_type}</span>
                        </div>
                    </aside>
                </div>

                <div class="flow" aria-label="Sync path">
                    <div class="flow-node">Stremio</div>
                    <div class="flow-arrow" aria-hidden="true"></div>
                    <div class="flow-node">AniSync</div>
                    <div class="flow-arrow" aria-hidden="true"></div>
                    <div class="flow-node">MAL / AniList</div>
                </div>

                <form id="manageForm" method="post" action="{form_action}">
                    <input type="hidden" name="meta_id" value="{safe_meta_id}">
                    <input type="hidden" name="content_type" value="{safe_type}">

                    <div class="form-grid">
                        <section class="panel" aria-labelledby="providers-title">
                            <div class="panel-head">
                                <div>
                                    <p class="step">1. Choose tracker</p>
                                    <h3 class="panel-title" id="providers-title">Send to services</h3>
                                </div>
                                <p class="panel-hint">Unavailable trackers are shown inactive.</p>
                            </div>

                            <div class="providers">
                                <label class="provider-card {mal_state}">
                                    <input class="provider-input" type="checkbox" name="provider_mal" value="1" {mal_checked} {mal_disabled}>
                                    <span class="provider-shell">
                                        <span>
                                            <span class="provider-name">MyAnimeList</span>
                                            <span class="provider-note">{mal_note}</span>
                                        </span>
                                        <span class="provider-toggle" aria-hidden="true"></span>
                                    </span>
                                </label>

                                <label class="provider-card {anilist_state}">
                                    <input class="provider-input" type="checkbox" name="provider_anilist" value="1" {anilist_checked} {anilist_disabled}>
                                    <span class="provider-shell">
                                        <span>
                                            <span class="provider-name">AniList</span>
                                            <span class="provider-note">{anilist_note}</span>
                                        </span>
                                        <span class="provider-toggle" aria-hidden="true"></span>
                                    </span>
                                </label>
                            </div>
                        </section>

                        <section class="panel" aria-labelledby="status-title">
                            <div class="panel-head">
                                <div>
                                    <p class="step">2. Set status</p>
                                    <h3 class="panel-title" id="status-title">Anime state</h3>
                                </div>
                                <p class="panel-hint">Choose exactly one list status.</p>
                            </div>

                            <fieldset class="status-grid">
                                <legend class="visually-hidden">Status</legend>
                                {status_cards}
                            </fieldset>
                        </section>

                        <section class="control-row" aria-label="Progress and score controls">
                            <div class="panel">
                                <div class="panel-head">
                                    <div>
                                        <p class="step">Progress</p>
                                        <h3 class="panel-title">Episode count</h3>
                                    </div>
                                </div>

                                <label class="visually-hidden" for="progressInput">Progress</label>
                                <div class="progress-box">
                                    <button class="round-btn" type="button" id="minusBtn" aria-label="Decrease progress">−</button>
                                    <input class="progress-num" id="progressInput" type="number" name="progress" min="0" inputmode="numeric" value="{default_progress}">
                                    <button class="round-btn" type="button" id="plusBtn" aria-label="Increase progress">+</button>
                                </div>
                            </div>

                            <div class="panel">
                                <div class="score-wrap">
                                    <div class="score-top">
                                        <div>
                                            <p class="step">Rating</p>
                                            <h3 class="panel-title">Your score</h3>
                                        </div>
                                        <div class="score-display">
                                            <span id="scoreValue">—</span> <small>/ 10</small>
                                        </div>
                                    </div>

                                    <fieldset class="score-list">
                                        <legend class="visually-hidden">Score</legend>
                                        {score_controls}
                                    </fieldset>
                                </div>
                            </div>
                        </section>

                        <section>
                            <div class="submit-panel">
                                <div class="submit-copy">
                                    <strong>3. Sync</strong>
                                    <span>Updates selected trackers only.</span>
                                </div>
                                <button class="sync-button" id="syncButton" type="submit">Sync Progress</button>
                            </div>

                            <div class="result" id="result" role="status" aria-live="polite" hidden></div>

                            <details>
                                <summary>Technical details</summary>
                                <dl class="details-grid">
                                    <div>
                                        <dt>ID</dt>
                                        <dd>{safe_meta_id}</dd>
                                    </div>
                                    <div>
                                        <dt>Type</dt>
                                        <dd>{safe_type}</dd>
                                    </div>
                                    <div>
                                        <dt>Kitsu</dt>
                                        <dd>{safe_kitsu_id}</dd>
                                    </div>
                                    <div>
                                        <dt>MAL</dt>
                                        <dd>{safe_mal_id}</dd>
                                    </div>
                                    <div>
                                        <dt>AniList</dt>
                                        <dd>{safe_anilist_id}</dd>
                                    </div>
                                    <div>
                                        <dt>Simkl</dt>
                                        <dd>{safe_simkl_id}</dd>
                                    </div>
                                </dl>
                            </details>
                        </section>
                    </div>
                </form>
            </div>
        </section>
    </main>

    <script>
        const form = document.getElementById("manageForm");
        const result = document.getElementById("result");
        const syncButton = document.getElementById("syncButton");
        const progressInput = document.getElementById("progressInput");
        const minusBtn = document.getElementById("minusBtn");
        const plusBtn = document.getElementById("plusBtn");
        const scoreValue = document.getElementById("scoreValue");

        function clampProgress() {{
            const parsed = Number.parseInt(progressInput.value || "0", 10);
            progressInput.value = Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
        }}

        function pulseProgress() {{
            progressInput.classList.remove("pulse");
            void progressInput.offsetWidth;
            progressInput.classList.add("pulse");
        }}

        minusBtn.addEventListener("click", () => {{
            clampProgress();
            progressInput.value = Math.max(0, Number(progressInput.value) - 1);
            pulseProgress();
        }});

        plusBtn.addEventListener("click", () => {{
            clampProgress();
            progressInput.value = Number(progressInput.value) + 1;
            pulseProgress();
        }});

        progressInput.addEventListener("blur", clampProgress);

        function syncScoreDisplay() {{
            const checked = document.querySelector('input[name="score"]:checked');
            const score = checked ? checked.value : "";

            scoreValue.textContent = score === "" ? "—" : score;

            document.querySelectorAll(".score-choice").forEach((choice) => {{
                const input = choice.querySelector("input");
                const active = score !== "" && input.value !== "" && Number(input.value) <= Number(score);

                choice.classList.toggle("is-filled", active);
                choice.classList.toggle("is-selected", input.checked);
            }});
        }}

        document.querySelectorAll('input[name="score"]').forEach((input) => {{
            input.addEventListener("change", syncScoreDisplay);
        }});

        syncScoreDisplay();

        form.addEventListener("submit", async (event) => {{
            if (!window.fetch) {{
                return;
            }}

            event.preventDefault();

            if (!form.reportValidity()) {{
                return;
            }}

            result.hidden = false;
            result.className = "result";
            result.textContent = "Syncing selected trackers…";

            const originalLabel = syncButton.textContent;
            syncButton.disabled = true;
            syncButton.textContent = "Syncing…";

            try {{
                const response = await fetch(form.action, {{
                    method: "POST",
                    body: new FormData(form),
                    headers: {{
                        "Accept": "application/json"
                    }}
                }});

                const data = await response.json().catch(() => ({{}}));

                result.className = response.ok ? "result is-success" : "result is-error";
                result.textContent = data.message || (response.ok ? "Progress synced." : "Update failed.");
            }} catch (error) {{
                result.className = "result is-error";
                result.textContent = "Update failed. Check your connection and try again.";
            }} finally {{
                syncButton.disabled = false;
                syncButton.textContent = originalLabel;
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