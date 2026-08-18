"""Renders report.json (from pytest-json-report) into a styled, self-contained
HTML dashboard. Kept dependency-free (no Jinja2) so it runs the same locally
and in CI with nothing beyond the stdlib.

Healing detection is data-driven, not hardcoded: pytest-json-report captures
each test's `logging` records verbatim (see test["call"]["log"]), and every
healed lookup logs a structured WARNING via
`locators.modular_locator_framework`'s `find_element()` — so a test only gets
tagged "healed" here because its own captured log actually says so. Add a
new test that heals and it shows up automatically; no name list to maintain.
"""

import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUS_META = {
    "passed": {"label": "Passed", "color": "good", "icon": "✓"},
    "failed": {"label": "Failed", "color": "critical", "icon": "✕"},
    "error": {"label": "Error", "color": "critical", "icon": "✕"},
    "skipped": {"label": "Skipped", "color": "warning", "icon": "●"},
}

# One entry per known test file, in the order the "realism ladder" should
# present them. A test file not listed here still renders fine — it just
# falls back to a humanized name with no description, appended at the end.
STAGE_META = {
    "modular_locator_framework": {
        "label": "Unit",
        "desc": "Strategies search a plain in-memory list &mdash; no browser at all. "
        "Proves the fallback chain and <span class=\"mono\">none_found</span> "
        "reporting are correct in isolation.",
    },
    "playwright_locator_strategy": {
        "label": "Synthetic Browser",
        "desc": "The same strategies, backed by a real Chromium page rendering an "
        "inline HTML snippet instead of a Python list.",
    },
    "real_page_navigation": {
        "label": "Real Page",
        "desc": "A public, unmodified login form. The submit button has no "
        "<span class=\"mono\">id</span>, so the engine has to actually fall "
        "through to a role-based strategy to succeed.",
    },
    "visual_match": {
        "label": "Visual Match",
        "desc": "OpenCV template matching against a screenshot &mdash; the "
        "last-resort strategy for elements with no reliable DOM attributes at all.",
    },
}
STAGE_ORDER = list(STAGE_META)

_HEALED_LOG_RE = re.compile(
    r"Locator healed for target '(?P<target>[^']+)': "
    r"\[(?P<failed>[^\]]*)\] failed, recovered via '(?P<recovered>[^']+)'"
)


def format_duration(seconds):
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m {secs:.1f}s"


def test_duration(test):
    return sum(test.get(phase, {}).get("duration", 0) for phase in ("setup", "call", "teardown"))


def module_stem(nodeid):
    file_part, _, _ = nodeid.partition("::")
    stem = Path(file_part).stem
    if stem.startswith("test_"):
        stem = stem[len("test_") :]
    return stem


def stage_label(stem):
    meta = STAGE_META.get(stem)
    return meta["label"] if meta else stem.replace("_", " ").title()


def test_display_name(nodeid):
    _, _, test_part = nodeid.partition("::")
    return test_part


def find_healing(test):
    """Return (failed_strategies, recovered_strategy) if this test's captured
    logs contain a healing WARNING, else None. Reads the real log text — never
    guesses from the test's name.
    """
    for phase in ("setup", "call", "teardown"):
        for record in test.get(phase, {}).get("log", []):
            if record.get("levelname") != "WARNING":
                continue
            m = _HEALED_LOG_RE.search(record.get("msg", ""))
            if m:
                failed = re.findall(r"'([^']+)'", m.group("failed"))
                return failed, m.group("recovered")
    return None


def status_pill(outcome):
    meta = STATUS_META.get(outcome, {"label": outcome.title(), "color": "warning", "icon": "?"})
    return (
        f'<span class="pill pill-{meta["color"]}">'
        f'<span class="pill-icon">{meta["icon"]}</span>{meta["label"]}</span>'
    )


def strategy_tag(test):
    healing = find_healing(test)
    if healing:
        failed, recovered = healing
        last_failed = html.escape(failed[-1]) if failed else "?"
        return f'<span class="strategy-tag healed">{last_failed} &rarr; {html.escape(recovered)}</span>'
    if test["outcome"] == "passed":
        return '<span class="strategy-tag primary">primary match</span>'
    return '<span class="strategy-tag primary">&mdash;</span>'


def build_stages(tests):
    by_stem = {}
    for test in tests:
        by_stem.setdefault(module_stem(test["nodeid"]), []).append(test)

    ordered = [s for s in STAGE_ORDER if s in by_stem]
    ordered += [s for s in by_stem if s not in STAGE_ORDER]

    stages = []
    for stem in ordered:
        stage_tests = by_stem[stem]
        meta = STAGE_META.get(stem, {})
        total = len(stage_tests)
        passed = sum(1 for t in stage_tests if t["outcome"] == "passed")
        pass_pct = round(passed / total * 100) if total else 0
        stages.append(
            {
                "num": f"{len(stages) + 1:02d}",
                "label": stage_label(stem),
                "desc": meta.get("desc", ""),
                "total": total,
                "pass_pct": pass_pct,
            }
        )
    return stages


def build_stage_cards(stages):
    cards = []
    for i, s in enumerate(stages):
        delay = 100 + i * 50
        cards.append(
            f"""
    <div class="stage rise" style="animation-delay: {delay}ms">
      <span class="stage-num">{s["num"]}</span>
      <h3 class="stage-title">{html.escape(s["label"])}</h3>
      <p class="stage-desc">{s["desc"]}</p>
      <div class="stage-foot"><span class="stage-count mono">{s["total"]} tests</span>
      <span class="stage-pass">{s["pass_pct"]}%</span></div>
    </div>"""
        )
    return "\n".join(cards)


def build_healed_rows(tests):
    rows = []
    for test in tests:
        healing = find_healing(test)
        if not healing:
            continue
        failed, recovered = healing
        last_failed = html.escape(failed[-1]) if failed else "?"
        rows.append(
            f"""
      <div class="heal-row">
        <span class="mono">{html.escape(test_display_name(test["nodeid"]))}</span>
        <span class="via-tag">{last_failed} &rarr; {html.escape(recovered)}</span>
      </div>"""
        )
    return "\n".join(rows)


def build_table_rows(tests):
    rows = []
    for test in tests:
        module = stage_label(module_stem(test["nodeid"]))
        name = test_display_name(test["nodeid"])
        duration = format_duration(test_duration(test))
        rows.append(
            f"""
        <tr>
          <td class="col-module">{html.escape(module)}</td>
          <td class="col-test mono">{html.escape(name)}</td>
          <td class="col-status">{status_pill(test["outcome"])}</td>
          <td class="col-strategy">{strategy_tag(test)}</td>
          <td class="col-duration mono">{duration}</td>
        </tr>"""
        )
    return "\n".join(rows)


def stat_tile(value, label, css_class="", accent_var="--border"):
    return f"""
    <div class="tile rise" style="--tile-accent: var({accent_var})">
      <div class="tile-value mono {css_class}">{value}</div>
      <div class="tile-label">{html.escape(label)}</div>
    </div>"""


def build_ticks(cx=88, cy=88, r_outer=86, r_inner_minor=80, r_inner_major=76, count=24):
    """Rangefinder-style tick marks around the hero scope ring, generated
    server-side so the page needs no JS beyond the theme toggle + ring fill."""
    ticks = []
    for i in range(count):
        angle = (i / count) * 2 * math.pi
        is_major = i % 6 == 0
        r_inner = r_inner_major if is_major else r_inner_minor
        x1, y1 = cx + r_outer * math.cos(angle), cy + r_outer * math.sin(angle)
        x2, y2 = cx + r_inner * math.cos(angle), cy + r_inner * math.sin(angle)
        cls = "tick major" if is_major else "tick"
        ticks.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" class="{cls}"></line>')
    return "\n".join(ticks)


def render(report):
    summary = report["summary"]
    tests = report["tests"]
    total = summary.get("total", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0) + summary.get("error", 0)
    skipped = summary.get("skipped", 0)
    pass_rate = (passed / total * 100) if total else 0
    duration = format_duration(report.get("duration", 0))
    generated = datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC")

    overall_status = "warning" if total == 0 else "critical" if failed > 0 else "good"

    stages = build_stages(tests)
    healed = [t for t in tests if find_healing(t)]
    strategies_exercised = len({module_stem(t["nodeid"]) for t in tests})

    ring_radius = 72
    circumference = 2 * math.pi * ring_radius
    ring_offset = circumference * (1 - pass_rate / 100)
    ticks = build_ticks()

    tiles = "".join(
        [
            stat_tile(total, "Total tests"),
            stat_tile(strategies_exercised, "Locator strategies exercised"),
            stat_tile(len(healed), "Self-healed fallbacks", "heal" if healed else "", "--heal"),
            stat_tile(duration, "Total duration"),
        ]
    )

    stage_cards = build_stage_cards(stages)
    healed_rows = build_healed_rows(tests)
    table_rows = build_table_rows(tests)

    spotlight = ""
    if healed:
        spotlight = f"""
  <div class="section-head">
    <p class="section-title">Self-healing spotlight</p>
    <p class="section-note">The differentiator, not just the pass/fail count</p>
  </div>
  <div class="spotlight rise" style="animation-delay: 100ms">
    <div class="spotlight-top">
      <span class="spotlight-tag">{len(healed)} of {total} tests healed</span>
    </div>
    <p class="spotlight-title">Detection, not silent magic</p>
    <p class="spotlight-body">When an element is found by anything other than the first
      strategy in the list, the engine logs a <span class="mono">WARNING</span> naming the
      target and which strategy ultimately recovered it. It doesn't rewrite your locators
      for you &mdash; it tells you the moment one has gone stale, which is the first honest
      step toward self-healing.</p>
    <div class="heal-list">{healed_rows}
    </div>
  </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Locator Framework &mdash; Test Dashboard</title>
<style>
  :root {{
    color-scheme: light;
    --page:        #faf8f3;
    --surface:     #ffffff;
    --surface-2:   #f3f0ea;
    --text-1:      #1c1a16;
    --text-2:      #5b564c;
    --text-3:      #8c867a;
    --border:      rgba(28,26,22,0.12);
    --border-2:    rgba(28,26,22,0.22);
    --grid-dot:    rgba(28,26,22,0.10);
    --accent:      #a97a1e;
    --accent-bg:   rgba(169,122,30,0.10);
    --accent-glow: rgba(169,122,30,0.28);
    --heal:        #00968c;
    --heal-bg:     rgba(0,150,140,0.09);
    --good:        #128a5f;
    --good-bg:     rgba(18,138,95,0.10);
    --warning:     #b8901a;
    --warning-bg:  rgba(184,144,26,0.12);
    --critical:    #d1272f;
    --critical-bg: rgba(209,39,47,0.10);
    --shadow: 0 1px 2px rgba(28,26,22,0.05), 0 16px 40px -16px rgba(28,26,22,0.16);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      color-scheme: dark;
      --page:        #0d0b09;
      --surface:     #1c1914;
      --surface-2:   #241f18;
      --text-1:      #f7f4ee;
      --text-2:      #c2bcae;
      --text-3:      #8c8577;
      --border:      rgba(247,244,238,0.12);
      --border-2:    rgba(247,244,238,0.22);
      --grid-dot:    rgba(247,244,238,0.07);
      --accent:      #d9a53d;
      --accent-bg:   rgba(184,132,42,0.18);
      --accent-glow: rgba(217,165,61,0.30);
      --heal:        #1f9e93;
      --heal-bg:     rgba(31,158,147,0.16);
      --good:        #1a9e78;
      --good-bg:     rgba(26,158,120,0.16);
      --warning:     #a8890f;
      --warning-bg:  rgba(168,137,15,0.18);
      --critical:    #c73f3a;
      --critical-bg: rgba(199,63,58,0.16);
      --shadow: 0 1px 2px rgba(0,0,0,0.30), 0 16px 40px -16px rgba(0,0,0,0.60);
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --page:        #0d0b09;
    --surface:     #1c1914;
    --surface-2:   #241f18;
    --text-1:      #f7f4ee;
    --text-2:      #c2bcae;
    --text-3:      #8c8577;
    --border:      rgba(247,244,238,0.12);
    --border-2:    rgba(247,244,238,0.22);
    --grid-dot:    rgba(247,244,238,0.07);
    --accent:      #d9a53d;
    --accent-bg:   rgba(184,132,42,0.18);
    --accent-glow: rgba(217,165,61,0.30);
    --heal:        #1f9e93;
    --heal-bg:     rgba(31,158,147,0.16);
    --good:        #1a9e78;
    --good-bg:     rgba(26,158,120,0.16);
    --warning:     #a8890f;
    --warning-bg:  rgba(168,137,15,0.18);
    --critical:    #c73f3a;
    --critical-bg: rgba(199,63,58,0.16);
    --shadow: 0 1px 2px rgba(0,0,0,0.30), 0 16px 40px -16px rgba(0,0,0,0.60);
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background-color: var(--page);
    background-image: radial-gradient(var(--grid-dot) 1px, transparent 1px);
    background-size: 26px 26px;
    color: var(--text-1);
    font-family: "Bahnschrift", "Segoe UI Variable Text", "Segoe UI", -apple-system,
                 system-ui, "Helvetica Neue", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .mono {{
    font-family: "Cascadia Mono", "Cascadia Code", Consolas, "SF Mono", "JetBrains Mono",
                 ui-monospace, "Courier New", monospace;
    font-variant-numeric: tabular-nums;
  }}
  a {{ color: inherit; }}
  .wrap {{ max-width: 1020px; margin: 0 auto; padding: 48px 24px 88px; }}

  @keyframes riseIn {{
    from {{ opacity: 0; transform: translateY(10px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
  }}
  @keyframes sweep {{
    from {{ transform: rotate(0deg); }}
    to   {{ transform: rotate(360deg); }}
  }}
  .rise {{ animation: riseIn 620ms cubic-bezier(.2,.7,.2,1) both; }}
  @media (prefers-reduced-motion: reduce) {{
    .rise {{ animation: none; }}
    .sweep-arc, .ring-value {{ animation: none !important; transition: none !important; }}
  }}

  .mark line, .mark circle {{ stroke: var(--{overall_status}); }}

  .masthead {{ display: flex; justify-content: space-between; align-items: flex-start;
    gap: 20px; flex-wrap: wrap; margin-bottom: 40px; }}
  .eyebrow {{ display: flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--text-3); margin-bottom: 14px; }}
  h1 {{ font-size: 34px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.08;
    margin: 0 0 10px; text-wrap: balance; }}
  .masthead-sub {{ color: var(--text-2); font-size: 14.5px; line-height: 1.6; margin: 0; max-width: 56ch; }}
  .masthead-sub .mono {{ color: var(--text-3); }}
  .masthead-actions {{ display: flex; gap: 8px; align-items: flex-start; }}
  .btn {{ display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-2);
    background: var(--surface); color: var(--text-2); border-radius: 8px; padding: 7px 12px;
    font-size: 12.5px; font-weight: 600; text-decoration: none; cursor: pointer; font-family: inherit; }}
  .btn:hover {{ color: var(--text-1); border-color: var(--text-3); }}
  .btn:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

  .hero {{ background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
    box-shadow: var(--shadow); padding: 36px 40px; display: flex; align-items: center;
    gap: 40px; flex-wrap: wrap; margin-bottom: 18px; position: relative; overflow: hidden; }}
  .scope {{ position: relative; width: 176px; height: 176px; flex-shrink: 0; }}
  .sweep-arc {{ position: absolute; inset: 0;
    background: conic-gradient(from 0deg, transparent 0deg, var(--accent-glow) 18deg, transparent 60deg);
    border-radius: 50%; animation: sweep 7s linear infinite; opacity: 0.7; }}
  .scope svg {{ position: relative; transform: rotate(-90deg); }}
  .tick {{ stroke: var(--border-2); stroke-width: 1.5; }}
  .tick.major {{ stroke: var(--text-3); stroke-width: 2; }}
  .ring-track {{ fill: none; stroke: var(--surface-2); stroke-width: 9; }}
  .ring-value {{ fill: none; stroke: var(--{overall_status}); stroke-width: 9; stroke-linecap: round;
    transition: stroke-dashoffset 1100ms cubic-bezier(.16,.8,.2,1); }}
  .scope-label {{ position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; }}
  .scope-pct {{ font-size: 36px; font-weight: 700; line-height: 1; letter-spacing: -0.01em; }}
  .scope-pct-sub {{ font-size: 9.5px; color: var(--text-3); margin-top: 5px; letter-spacing: 0.12em; font-weight: 700; }}
  .hero-meta {{ flex: 1; min-width: 240px; }}
  .hero-headline {{ font-size: 16px; font-weight: 700; margin: 0 0 6px; }}
  .hero-detail {{ font-size: 13.5px; color: var(--text-2); line-height: 1.6; margin: 0; max-width: 46ch; }}
  .hero-detail .mono {{ color: var(--text-1); font-weight: 600; }}

  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px; margin-bottom: 32px; }}
  .tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
    padding: 18px 20px; position: relative; overflow: hidden; }}
  .tile::before {{ content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: var(--tile-accent, var(--border)); }}
  .tile-value {{ font-size: 27px; font-weight: 700; line-height: 1.1; margin-bottom: 5px; }}
  .tile-value.heal {{ color: var(--heal); }}
  .tile-label {{ font-size: 11.5px; color: var(--text-3); letter-spacing: 0.02em; }}

  .section-head {{ display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px; margin: 48px 0 16px; }}
  .section-title {{ display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-3); margin: 0; }}
  .section-title::before {{ content: ""; width: 14px; height: 1.5px; background: var(--accent); }}
  .section-note {{ font-size: 12.5px; color: var(--text-3); margin: 0; }}

  .ladder {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; }}
  .stage {{ background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
    padding: 20px; position: relative; transition: border-color 200ms, transform 200ms; }}
  .stage:hover {{ border-color: var(--border-2); transform: translateY(-2px); }}
  .stage-num {{ font-family: "Cascadia Mono", Consolas, ui-monospace, monospace; font-size: 26px;
    font-weight: 700; color: var(--accent-bg); -webkit-text-stroke: 1px var(--accent);
    display: block; margin-bottom: 6px; letter-spacing: -0.02em; }}
  .stage-title {{ font-size: 14.5px; font-weight: 700; margin: 0 0 6px; }}
  .stage-desc {{ font-size: 12px; color: var(--text-2); margin: 0 0 16px; line-height: 1.5; min-height: 54px; }}
  .stage-foot {{ display: flex; align-items: center; justify-content: space-between; font-size: 12px;
    padding-top: 12px; border-top: 1px solid var(--border); }}
  .stage-count {{ color: var(--text-1); font-weight: 700; }}
  .stage-pass {{ color: var(--good); font-weight: 700; }}

  .spotlight {{ background: var(--surface); border: 1px solid var(--border); border-radius: 18px;
    padding: 28px 32px; position: relative; overflow: hidden; }}
  .spotlight::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--heal); }}
  .spotlight::after {{ content: ""; position: absolute; right: -60px; top: -60px; width: 220px; height: 220px;
    background: radial-gradient(circle, var(--heal-bg), transparent 70%); pointer-events: none; }}
  .spotlight-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; position: relative; }}
  .spotlight-tag {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--heal); background: var(--heal-bg); padding: 4px 10px; border-radius: 999px; }}
  .spotlight-title {{ font-size: 17px; font-weight: 700; margin: 0 0 10px; position: relative; }}
  .spotlight-body {{ font-size: 13.5px; color: var(--text-2); line-height: 1.65; max-width: 66ch; margin: 0 0 18px; position: relative; }}
  .heal-list {{ display: flex; flex-direction: column; gap: 8px; position: relative; }}
  .heal-row {{ display: flex; align-items: center; gap: 10px; font-size: 12.5px; padding: 10px 14px;
    border-radius: 10px; background: var(--surface-2); border: 1px solid var(--border); transition: border-color 150ms; }}
  .heal-row:hover {{ border-color: var(--heal); }}
  .heal-row .mono {{ color: var(--text-1); font-weight: 600; flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .heal-row .via-tag {{ font-size: 10.5px; font-weight: 700; color: var(--heal); background: var(--surface);
    padding: 3px 8px; border-radius: 6px; white-space: nowrap; border: 1px solid var(--heal-bg);
    font-family: "Cascadia Mono", Consolas, ui-monospace, monospace; }}

  .table-scroll {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 16px; background: var(--surface); }}
  table {{ width: 100%; border-collapse: collapse; min-width: 660px; }}
  thead th {{ text-align: left; font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.07em; color: var(--text-3); padding: 14px 18px; border-bottom: 1px solid var(--border);
    background: var(--surface-2); }}
  tbody td {{ padding: 12px 18px; border-bottom: 1px solid var(--border); font-size: 13.5px; vertical-align: middle; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr {{ transition: background 120ms; }}
  tbody tr:hover td {{ background: var(--surface-2); }}
  .col-module {{ color: var(--text-3); font-size: 12px; width: 15%; white-space: nowrap; }}
  .col-test {{ font-weight: 500; }}
  .col-status {{ width: 100px; }}
  .col-strategy {{ width: 220px; }}
  .col-duration {{ width: 90px; text-align: right; }}

  .pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 999px;
    font-size: 11.5px; font-weight: 700; }}
  .pill-good {{ background: var(--good-bg); color: var(--good); }}
  .pill-warning {{ background: var(--warning-bg); color: var(--warning); }}
  .pill-critical {{ background: var(--critical-bg); color: var(--critical); }}
  .pill-icon {{ font-size: 10px; }}

  .strategy-tag {{ display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 5px; white-space: nowrap;
    font-family: "Cascadia Mono", Consolas, ui-monospace, monospace; }}
  .strategy-tag.healed {{ color: var(--heal); background: var(--heal-bg); }}
  .strategy-tag.primary {{ color: var(--text-3); background: var(--surface-2); }}

  footer {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
  .chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .chip {{ font-size: 11px; font-weight: 600; color: var(--text-2); background: var(--surface-2);
    border: 1px solid var(--border); padding: 4px 10px; border-radius: 999px; }}
  .footer-links {{ display: flex; gap: 16px; font-size: 12.5px; }}
  .footer-links a {{ color: var(--text-2); text-decoration: none; border-bottom: 1px solid var(--border-2); }}
  .footer-links a:hover {{ color: var(--text-1); }}

  @media (max-width: 480px) {{ .col-module {{ display: none; }} }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="masthead rise">
      <div>
        <div class="eyebrow">
          <svg class="mark" width="14" height="14" viewBox="0 0 14 14" fill="none">
            <circle cx="7" cy="7" r="5.5" stroke-width="1.3"></circle>
            <line x1="7" y1="0.5" x2="7" y2="3.2" stroke-width="1.3"></line>
            <line x1="7" y1="10.8" x2="7" y2="13.5" stroke-width="1.3"></line>
            <line x1="0.5" y1="7" x2="3.2" y2="7" stroke-width="1.3"></line>
            <line x1="10.8" y1="7" x2="13.5" y2="7" stroke-width="1.3"></line>
          </svg>
          CI Test Telemetry
        </div>
        <h1>Playwright Modular Locator Framework</h1>
        <p class="masthead-sub">Self-healing element lookup for UI test automation &mdash; a
          prioritized chain of locator strategies, with visibility into which one actually
          locked onto each element. Generated <span class="mono">{generated}</span></p>
      </div>
      <div class="masthead-actions">
        <button class="btn" id="theme-toggle" type="button">Toggle theme</button>
      </div>
    </header>

    <div class="hero rise" style="animation-delay: 60ms">
      <div class="scope">
        <div class="sweep-arc"></div>
        <svg width="176" height="176" viewBox="0 0 176 176">
          <g>{ticks}</g>
          <circle class="ring-track" cx="88" cy="88" r="{ring_radius}"></circle>
          <circle class="ring-value" id="ring" cx="88" cy="88" r="{ring_radius}"
            stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{circumference:.2f}"
            data-target-offset="{ring_offset:.2f}"></circle>
        </svg>
        <div class="scope-label">
          <div class="scope-pct mono">{pass_rate:.0f}%</div>
          <div class="scope-pct-sub">PASS RATE</div>
        </div>
      </div>
      <div class="hero-meta">
        <p class="hero-headline">{passed} of {total} tests locked on &middot; {failed} failed &middot; {skipped} skipped</p>
        <p class="hero-detail">Full suite ran in <span class="mono">{duration}</span> across
          {len(stages)} level{'' if len(stages) == 1 else 's'} of realism &mdash; from
          pure-Python logic to a real, live page.</p>
      </div>
    </div>

    <div class="tiles">{tiles}
    </div>

    <div class="section-head">
      <p class="section-title">Realism ladder</p>
      <p class="section-note">Same Strategy pattern, staged levels of proof</p>
    </div>
    <div class="ladder">{stage_cards}
    </div>
    {spotlight}

    <div class="section-head">
      <p class="section-title">Full results</p>
      <p class="section-note">{total} tests &middot; {duration}</p>
    </div>
    <div class="table-scroll rise" style="animation-delay: 150ms">
      <table>
        <thead>
          <tr>
            <th class="col-module">Module</th>
            <th class="col-test">Test</th>
            <th class="col-status">Result</th>
            <th class="col-strategy">Resolved via</th>
            <th class="col-duration">Duration</th>
          </tr>
        </thead>
        <tbody>{table_rows}
        </tbody>
      </table>
    </div>

    <footer>
      <div class="chips">
        <span class="chip">Python</span>
        <span class="chip">Playwright</span>
        <span class="chip">pytest</span>
        <span class="chip">OpenCV</span>
        <span class="chip">GitHub Actions</span>
      </div>
      <div class="footer-links">
        <a href="report.html">Detailed report</a>
      </div>
    </footer>
  </div>

  <script>
    function toggleTheme() {{
      const root = document.documentElement;
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    }}
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
    const saved = localStorage.getItem('theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);

    const ring = document.getElementById('ring');
    requestAnimationFrame(() => {{ ring.style.strokeDashoffset = ring.dataset.targetOffset; }});
  </script>
</body>
</html>
"""


def main():
    report_path = sys.argv[1] if len(sys.argv) > 1 else "report.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "dashboard.html"

    report = json.loads(Path(report_path).read_text())
    html_out = render(report)
    Path(output_path).write_text(html_out, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
