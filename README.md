# Playwright Modular Locator Framework

A pluggable, testable locator framework for UI test automation, built around
the **Strategy design pattern**. Instead of hard-coding "how to find an
element," each way of finding one (exact ID, visible text, class+tag,
accessibility role, ...) is its own independent, swappable module. The core
engine never changes — you just plug different strategies in.

The framework is demonstrated three ways in this repo, in increasing order
of realism:

1. **Pure Python** — strategies search a plain in-memory list (`dict`
   objects standing in for DOM elements). No browser involved at all.
2. **Playwright against synthetic HTML** — the same strategy pattern,
   backed by a real Chromium page rendering an inline HTML snippet.
3. **Playwright against a real, live website** — a public login form
   (`the-internet.herokuapp.com/login`), including a fallback scenario
   where the primary locator (`id`) doesn't exist on the real button and
   the engine falls through to a role-based strategy.

---

## Why this exists

Real UI automation breaks constantly because locators go stale — an `id`
gets renamed, a class changes, markup gets restructured. Most test suites
hard-code a single locator per element and simply fail when it changes.

This framework instead tries a **prioritized list of strategies** per
element and uses the first one that matches, so a single stale locator
doesn't fail the test — and you get visibility into *which* strategy
ended up finding the element (useful for noticing when your "primary"
locator has quietly gone stale).

---

## Project structure

```
python-playwright/
├── locators/
│   ├── __init__.py
│   ├── modular_locator_framework.py   # Core engine + Strategy pattern + in-memory demo
│   └── playwright_strategies.py       # Playwright-backed strategy implementations
├── tests/
│   ├── __init__.py
│   ├── test_modular_locator_framework.py    # Pure-Python unit tests (no browser)
│   ├── test_playwright_locator_strategy.py  # Playwright + synthetic inline HTML
│   └── test_real_page_navigation.py         # Playwright + a real, live webpage
├── venv/                # Local virtual environment (not committed)
├── pytest.ini            # testpaths config
├── requirements.txt      # Frozen dependency versions
├── .gitignore
└── README.md
```

---

## The core design

### 1. The contract (`locators/modular_locator_framework.py`)

Every locator strategy implements one method:

```python
class LocatorStrategy(ABC):
    @abstractmethod
    def find(self, dom, target):
        """Return the matching element, or None if not found."""

    @property
    @abstractmethod
    def name(self):
        """A short name for this strategy, used in reporting."""
```

### 2. The engine

```python
class ModularLocatorEngine:
    def __init__(self, strategies):
        self.strategies = strategies

    def find_element(self, dom, target):
        for strategy in self.strategies:
            result = strategy.find(dom, target)
            if result:
                return {"target": target, "strategy_used": strategy.name, "element": result}
        return {"target": target, "strategy_used": "none_found", "element": None}
```

The engine has **zero knowledge** of Selenium, Playwright, or anything else
— it only ever calls `strategy.find(dom, target)`. That's what makes the
underlying tool swappable without touching the engine.

### 3. In-memory strategies (for learning/demo purposes)

- `ExactIdStrategy` — matches on `id`
- `VisibleTextStrategy` — matches on visible text
- `ClassAndTagStrategy` — matches on CSS class + tag
- `NearbyLabelStrategy` — matches on an ARIA label

### 4. Playwright strategies (`locators/playwright_strategies.py`)

The same pattern, backed by a real `Page` object:

- `PlaywrightExactIdStrategy` — `page.locator(f"#{target}")`
- `PlaywrightVisibleTextStrategy` — `page.get_by_text(...)`
- `PlaywrightRoleStrategy` — `page.get_by_role(...)`, matching by
  accessibility role + accessible name (often the most resilient option,
  since roles rarely change even when markup does)

Swapping from Selenium to Playwright (or anything else) only means writing
new strategy classes — `ModularLocatorEngine` itself never changes.

---

## Setup from scratch

**Prerequisites:** Python 3.10+ (developed against 3.14).

```bash
cd python-playwright

# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install the Chromium browser binary Playwright drives
python -m playwright install chromium
```

---

## Running the tests

Run everything (headless, default):

```bash
pytest -v
```

Run only the pure-Python unit tests (fastest, no browser):

```bash
pytest tests/test_modular_locator_framework.py -v
```

Run **headed** so you can actually watch Chromium open and interact with
pages (add `--slowmo` in milliseconds to slow it down for observation):

```bash
pytest --headed --slowmo 500 -v
```

Generate a browsable HTML report (self-contained — open the file directly,
no server needed):

```bash
pytest --html=report.html --self-contained-html
```

Then open `report.html` in any browser to see pass/fail status per test.

---

## What each test file actually validates

| File | What it proves |
|---|---|
| `test_modular_locator_framework.py` | The engine correctly falls through a chain of strategies, and correctly reports `none_found` when nothing matches — pure logic, no browser. |
| `test_playwright_locator_strategy.py` | The same fallback behavior works when strategies are backed by a real Chromium page instead of a Python list. |
| `test_real_page_navigation.py` | The framework works end-to-end against a real, unmodified website: fills in a login form by `id`, and — because the real submit button has no `id` — falls back to a role-based (`PlaywrightRoleStrategy`) lookup to find and click it, then asserts on the resulting page state. A second test verifies a failed-login flash message via `PlaywrightVisibleTextStrategy`. |

As an end user, the meaningful signal is the **pass/fail result and which
`strategy_used` was reported** — not what flashes on screen in headed mode
(headless is the default and is what CI would run).

---

## Extending the framework

To add a new locator strategy (e.g. CSS selector, XPath, test-id
attribute), implement `LocatorStrategy`:

```python
class PlaywrightTestIdStrategy(LocatorStrategy):
    name = "playwright_test_id"

    def __init__(self, page):
        self.page = page

    def find(self, dom, target):
        locator = self.page.get_by_test_id(target)
        if locator.count() > 0:
            return locator.first
        return None
```

Then plug it into any engine's strategy list, in whatever priority order
makes sense:

```python
engine = ModularLocatorEngine(strategies=[
    PlaywrightExactIdStrategy(page),
    PlaywrightTestIdStrategy(page),
    PlaywrightRoleStrategy(page, role="button", accessible_name="Submit"),
])
```

No other code needs to change.
