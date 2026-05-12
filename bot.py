"""
Playwright bot for solving nonograms on nemonemologic.com.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser

from solver import solve, print_grid, FILLED, CROSS


BASE_URL = "https://nemonemologic.com/ko/play_logic.php"


# ---------------------------------------------------------------------------
# DOM extraction helpers
# ---------------------------------------------------------------------------

_JS_EXTRACT = """
() => {
    // Try to locate the puzzle data from common global variable names.
    const candidates = ['puzzle', 'nonogram', 'game', 'logic', 'qdata', 'puzzleData', 'gData'];
    for (const name of candidates) {
        if (window[name]) return { source: name, data: window[name] };
    }

    // Fallback: scan all window properties for objects with row/col clue shape.
    for (const key of Object.keys(window)) {
        try {
            const v = window[key];
            if (v && typeof v === 'object' && !Array.isArray(v)) {
                if ((v.rowHint || v.row_hint || v.rows || v.rowClues) &&
                    (v.colHint || v.col_hint || v.cols || v.colClues)) {
                    return { source: key, data: v };
                }
            }
        } catch (_) {}
    }
    return null;
}
"""

_JS_GRID_INFO = """
() => {
    // Find the puzzle table or canvas.
    // nemonemologic uses a <table id="puzzle_table"> or similar.
    const table = document.querySelector('table#puzzle_table') ||
                  document.querySelector('table.puzzle') ||
                  document.querySelector('table[class*="logic"]') ||
                  document.querySelector('table[class*="puzzle"]');
    if (!table) return null;

    const rect = table.getBoundingClientRect();
    const rows = table.querySelectorAll('tr');
    const info = { tableRect: rect, rowCount: rows.length, cells: [] };

    rows.forEach((tr, ri) => {
        const tds = tr.querySelectorAll('td');
        tds.forEach((td, ci) => {
            const r = td.getBoundingClientRect();
            info.cells.push({
                ri, ci,
                x: r.x + r.width / 2,
                y: r.y + r.height / 2,
                classes: td.className,
                id: td.id,
                dataset: Object.fromEntries(Object.entries(td.dataset)),
            });
        });
    });
    return info;
}
"""

_JS_CLUES_FROM_DOM = """
() => {
    // Parse hints displayed in <td class="...hint..."> cells.
    // nemonemologic structure:
    //   top-left corner cell  (empty)
    //   top row = column hints
    //   left column = row hints
    //   body = puzzle cells
    //
    // Attempt 1: look for cells with data-rnum / data-cnum attributes.
    const allCells = Array.from(document.querySelectorAll('td'));
    const rowHintCells = allCells.filter(td => td.dataset.rnum !== undefined && td.dataset.cnum === undefined);
    const colHintCells = allCells.filter(td => td.dataset.cnum !== undefined && td.dataset.rnum === undefined);

    if (rowHintCells.length > 0 || colHintCells.length > 0) {
        // group by row / col index
        const byRow = {};
        rowHintCells.forEach(td => {
            const r = parseInt(td.dataset.rnum);
            if (!byRow[r]) byRow[r] = [];
            const n = parseInt(td.textContent.trim());
            if (!isNaN(n)) byRow[r].push(n);
        });
        const byCol = {};
        colHintCells.forEach(td => {
            const c = parseInt(td.dataset.cnum);
            if (!byCol[c]) byCol[c] = [];
            const n = parseInt(td.textContent.trim());
            if (!isNaN(n)) byCol[c].push(n);
        });
        const rowClues = Object.keys(byRow).sort((a,b)=>a-b).map(k => byRow[k]);
        const colClues = Object.keys(byCol).sort((a,b)=>a-b).map(k => byCol[k]);
        if (rowClues.length && colClues.length) {
            return { rowClues, colClues, method: 'dataset' };
        }
    }

    // Attempt 2: nemonemologic specific structure
    // The hint cells typically have class containing 'num' or 'hint'
    const hintCells = allCells.filter(td =>
        /hint|hnum|rnum|cnum|clue/.test(td.className)
    );

    if (hintCells.length > 0) {
        const rows = new Map(), cols = new Map();
        hintCells.forEach(td => {
            const cls = td.className;
            const text = td.textContent.trim();
            const num = parseInt(text);
            if (isNaN(num) || num <= 0) return;

            const rm = cls.match(/r(\d+)/);
            const cm = cls.match(/c(\d+)/);
            if (rm && !cm) {
                const r = parseInt(rm[1]);
                if (!rows.has(r)) rows.set(r, []);
                rows.get(r).push(num);
            } else if (cm && !rm) {
                const c = parseInt(cm[1]);
                if (!cols.has(c)) cols.set(c, []);
                cols.get(c).push(num);
            }
        });
        if (rows.size && cols.size) {
            const rowClues = [...rows.keys()].sort((a,b)=>a-b).map(k => rows.get(k));
            const colClues = [...cols.keys()].sort((a,b)=>a-b).map(k => cols.get(k));
            return { rowClues, colClues, method: 'class-rXcX' };
        }
    }

    return null;
}
"""

_JS_PUZZLE_CELLS = """
() => {
    // Find clickable puzzle body cells (not hint cells).
    // They typically have data-row + data-col, or id like 'cell_R_C'.
    const allTds = Array.from(document.querySelectorAll('td'));

    // Try data-row / data-col
    let cells = allTds.filter(td => td.dataset.row !== undefined && td.dataset.col !== undefined);
    if (cells.length) {
        return cells.map(td => {
            const r = td.getBoundingClientRect();
            return {
                row: parseInt(td.dataset.row),
                col: parseInt(td.dataset.col),
                x: r.x + r.width/2,
                y: r.y + r.height/2,
                classes: td.className,
            };
        });
    }

    // Try id pattern cell_R_C or tdR_C
    cells = allTds.filter(td => /^(cell|td)_?\d+_\d+$/.test(td.id));
    if (cells.length) {
        return cells.map(td => {
            const m = td.id.match(/(\d+)[_\-](\d+)/);
            const r = td.getBoundingClientRect();
            return {
                row: parseInt(m[1]),
                col: parseInt(m[2]),
                x: r.x + r.width/2,
                y: r.y + r.height/2,
                classes: td.className,
            };
        });
    }

    return null;
}
"""


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------

class NonogramBot:
    def __init__(self, headless: bool = False, slow_mo: int = 50):
        self.headless = headless
        self.slow_mo = slow_mo

    async def run(self, puzzle_id: int):
        url = f"{BASE_URL}?quid={puzzle_id}"
        print(f"[bot] Opening puzzle {puzzle_id}: {url}")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
            page = await browser.new_page(viewport={"width": 1400, "height": 900})

            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(2)  # let JS fully render

            row_clues, col_clues = await self._extract_clues(page)
            if row_clues is None:
                print("[bot] ERROR: Could not extract clues from the page.")
                await self._save_debug(page, puzzle_id)
                await browser.close()
                return

            print(f"[bot] Row clues ({len(row_clues)} rows): {row_clues}")
            print(f"[bot] Col clues ({len(col_clues)} cols): {col_clues}")

            print("[bot] Solving...")
            grid = solve(row_clues, col_clues)
            if grid is None:
                print("[bot] ERROR: Puzzle has no solution.")
                await browser.close()
                return

            print("[bot] Solution:")
            print_grid(grid)

            await self._fill_grid(page, grid, row_clues, col_clues)

            print("[bot] Done! Submitting...")
            await self._submit(page)

            await asyncio.sleep(3)
            await self._save_debug(page, puzzle_id, suffix="result")
            await browser.close()

    # ------------------------------------------------------------------
    # Clue extraction
    # ------------------------------------------------------------------

    async def _extract_clues(self, page: Page):
        """Try multiple strategies to extract row/col clues."""

        # Strategy 1: global JS variable
        data = await page.evaluate(_JS_EXTRACT)
        if data:
            print(f"[bot] Found puzzle data in window.{data['source']}")
            clues = self._parse_global_data(data["data"])
            if clues:
                return clues

        # Strategy 2: parse DOM hint cells
        clues_dom = await page.evaluate(_JS_CLUES_FROM_DOM)
        if clues_dom:
            print(f"[bot] Extracted clues from DOM (method: {clues_dom['method']})")
            return clues_dom["rowClues"], clues_dom["colClues"]

        # Strategy 3: parse page HTML with regex
        content = await page.content()
        clues = self._parse_html_regex(content)
        if clues:
            print("[bot] Extracted clues via HTML regex")
            return clues

        return None, None

    def _parse_global_data(self, data: dict):
        """Try to extract row/col clues from a JS object."""
        def normalize(v):
            if isinstance(v, list):
                if all(isinstance(x, int) for x in v):
                    return [v]  # single clue row
                return [row if isinstance(row, list) else [row] for row in v]
            return None

        for row_key in ("rowHint", "row_hint", "rows", "rowClues", "row"):
            for col_key in ("colHint", "col_hint", "cols", "colClues", "col"):
                if row_key in data and col_key in data:
                    r = normalize(data[row_key])
                    c = normalize(data[col_key])
                    if r and c:
                        return r, c
        return None

    def _parse_html_regex(self, html: str):
        """Last-resort: find clue arrays in JS inside the HTML."""
        # Look for patterns like: var rowHint = [[1,2],[3],...];
        patterns = [
            r"(?:rowHint|row_hint|rowClues)\s*=\s*(\[\[.*?\]\])",
            r"(?:colHint|col_hint|colClues)\s*=\s*(\[\[.*?\]\])",
        ]
        found = {}
        for p in patterns:
            m = re.search(p, html, re.DOTALL)
            if m:
                key = "row" if "row" in p.lower() else "col"
                try:
                    found[key] = json.loads(m.group(1))
                except Exception:
                    pass
        if "row" in found and "col" in found:
            return found["row"], found["col"]
        return None

    # ------------------------------------------------------------------
    # Grid filling
    # ------------------------------------------------------------------

    async def _fill_grid(
        self,
        page: Page,
        grid: list[list[int]],
        row_clues: list[list[int]],
        col_clues: list[list[int]],
    ):
        rows = len(grid)
        cols = len(grid[0])

        # Get cell positions
        cells_info = await page.evaluate(_JS_PUZZLE_CELLS)

        if cells_info:
            print(f"[bot] Found {len(cells_info)} puzzle cells via JS")
            cell_map = {(c["row"], c["col"]): c for c in cells_info}
            await self._click_cells_by_map(page, grid, cell_map)
        else:
            # Fallback: try to click by CSS selector
            print("[bot] Falling back to CSS selector strategy")
            await self._click_cells_by_selector(page, grid, rows, cols)

    async def _click_cells_by_map(self, page: Page, grid: list[list[int]], cell_map: dict):
        """Click cells using precomputed (x, y) positions."""
        # Ensure FILL tool is selected (click the fill/black tool button)
        await self._select_fill_tool(page)

        for (r, c), info in cell_map.items():
            if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
                val = grid[r][c]
                if val == FILLED:
                    await page.mouse.click(info["x"], info["y"])
                    await asyncio.sleep(0.02)

    async def _click_cells_by_selector(self, page: Page, grid: list[list[int]], rows: int, cols: int):
        """Try common CSS selector patterns to click cells."""
        await self._select_fill_tool(page)

        for r in range(rows):
            for c in range(cols):
                if grid[r][c] != FILLED:
                    continue
                # Try common id/class patterns
                selectors = [
                    f'td[data-row="{r}"][data-col="{c}"]',
                    f'#cell_{r}_{c}',
                    f'#td{r}_{c}',
                    f'td.cell.r{r}.c{c}',
                ]
                for sel in selectors:
                    try:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.click()
                            await asyncio.sleep(0.02)
                            break
                    except Exception:
                        continue

    async def _select_fill_tool(self, page: Page):
        """Click the fill (black block) tool button."""
        selectors = [
            'button[title*="채우기"]',
            'button[title*="fill"]',
            'button[title*="Fill"]',
            '.tool_fill',
            '#tool_fill',
            'img[alt*="채우기"]',
            'img[alt*="fill"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    await asyncio.sleep(0.1)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    async def _submit(self, page: Page):
        selectors = [
            'button:has-text("제출")',
            'input[value="제출"]',
            '#btn_submit',
            '.btn_submit',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.click()
                    return
            except Exception:
                continue
        print("[bot] WARNING: Could not find submit button")

    # ------------------------------------------------------------------
    # Debug helpers
    # ------------------------------------------------------------------

    async def _save_debug(self, page: Page, puzzle_id: int, suffix: str = "debug"):
        path = Path(f"debug_{puzzle_id}_{suffix}.png")
        await page.screenshot(path=str(path), full_page=True)
        print(f"[bot] Screenshot saved: {path}")


# ---------------------------------------------------------------------------
# Inspect mode: print DOM info without solving (for debugging)
# ---------------------------------------------------------------------------

async def inspect_puzzle(puzzle_id: int):
    """Open puzzle in headed browser and dump DOM info to help debug."""
    url = f"{BASE_URL}?quid={puzzle_id}"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, slow_mo=100)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto(url, wait_until="networkidle")
        await asyncio.sleep(3)

        print("=== Global puzzle variable ===")
        data = await page.evaluate(_JS_EXTRACT)
        print(json.dumps(data, indent=2, ensure_ascii=False) if data else "Not found")

        print("\n=== DOM clues ===")
        clues = await page.evaluate(_JS_CLUES_FROM_DOM)
        print(json.dumps(clues, indent=2, ensure_ascii=False) if clues else "Not found")

        print("\n=== Puzzle cells (first 10) ===")
        cells = await page.evaluate(_JS_PUZZLE_CELLS)
        if cells:
            print(json.dumps(cells[:10], indent=2))
        else:
            print("Not found")

        await page.screenshot(path=f"inspect_{puzzle_id}.png", full_page=True)
        print(f"\nScreenshot: inspect_{puzzle_id}.png")

        input("Press Enter to close browser...")
        await browser.close()
