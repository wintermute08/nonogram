"""
Nonogram solver using line-solving (overlap method) + backtracking.
"""

EMPTY = 0
FILLED = 1
CROSS = -1


def solve(row_clues: list[list[int]], col_clues: list[list[int]]) -> list[list[int]] | None:
    """
    Solve a nonogram puzzle.

    Args:
        row_clues: list of clue lists for each row, e.g. [[3], [1,1], ...]
        col_clues: list of clue lists for each column

    Returns:
        2D grid with FILLED (1) and CROSS (-1), or None if unsolvable.
    """
    rows = len(row_clues)
    cols = len(col_clues)
    grid = [[EMPTY] * cols for _ in range(rows)]

    changed = True
    while changed:
        changed = False
        for r in range(rows):
            line = grid[r]
            new_line = solve_line(row_clues[r], line)
            if new_line is None:
                return None
            if new_line != line:
                grid[r] = new_line
                changed = True

        for c in range(cols):
            line = [grid[r][c] for r in range(rows)]
            new_line = solve_line(col_clues[c], line)
            if new_line is None:
                return None
            if new_line != line:
                for r in range(rows):
                    grid[r][c] = new_line[r]
                changed = True

    if any(EMPTY in row for row in grid):
        return _backtrack(grid, row_clues, col_clues, rows, cols)

    return grid


def solve_line(clues: list[int], line: list[int]) -> list[int] | None:
    """
    Apply overlap/interval method to a single line.
    Returns updated line or None if contradiction.
    """
    n = len(line)
    if clues == [0] or clues == []:
        result = [CROSS] * n
        for i, v in enumerate(line):
            if v == FILLED:
                return None
        return result

    leftmost = _place_leftmost(clues, line, n)
    rightmost = _place_rightmost(clues, line, n)

    if leftmost is None or rightmost is None:
        return None

    result = list(line)
    for i in range(n):
        if leftmost[i] == FILLED and rightmost[i] == FILLED:
            if result[i] == CROSS:
                return None
            result[i] = FILLED
        elif leftmost[i] == CROSS and rightmost[i] == CROSS:
            if result[i] == FILLED:
                return None
            result[i] = CROSS

    return result


def _place_leftmost(clues: list[int], line: list[int], n: int) -> list[int] | None:
    """Place blocks as far left as possible."""
    pos = [0] * len(clues)
    cur = 0
    for i, length in enumerate(clues):
        if i > 0:
            cur += 1  # gap between blocks
        start = cur
        # skip past CROSS cells
        while start < n and line[start] == CROSS:
            start += 1
        # find a valid placement starting at start
        placed = False
        for s in range(start, n - length + 1):
            if _can_place(line, s, length, n):
                pos[i] = s
                cur = s + length
                placed = True
                break
        if not placed:
            return None

    return _build_from_positions(pos, clues, n)


def _place_rightmost(clues: list[int], line: list[int], n: int) -> list[int] | None:
    """Place blocks as far right as possible."""
    pos = [0] * len(clues)
    cur = n - 1
    for i in range(len(clues) - 1, -1, -1):
        length = clues[i]
        end = cur
        # skip past CROSS cells from the right
        while end >= 0 and line[end] == CROSS:
            end -= 1
        # find a valid placement ending at end
        placed = False
        for e in range(end, length - 2, -1):
            s = e - length + 1
            if s >= 0 and _can_place(line, s, length, n):
                pos[i] = s
                cur = s - 2  # leave gap
                placed = True
                break
        if not placed:
            return None

    return _build_from_positions(pos, clues, n)


def _can_place(line: list[int], start: int, length: int, n: int) -> bool:
    """Check if a block of given length can be placed starting at start."""
    if start + length > n:
        return False
    for i in range(start, start + length):
        if line[i] == CROSS:
            return False
    after = start + length
    if after < n and line[after] == FILLED:
        return False
    return True


def _build_from_positions(positions: list[int], clues: list[int], n: int) -> list[int]:
    """Build a line given block start positions."""
    result = [CROSS] * n
    for i, s in enumerate(positions):
        for j in range(clues[i]):
            result[s + j] = FILLED
    return result


def _backtrack(
    grid: list[list[int]],
    row_clues: list[list[int]],
    col_clues: list[list[int]],
    rows: int,
    cols: int,
) -> list[list[int]] | None:
    """Backtracking search for remaining EMPTY cells."""
    # Find first EMPTY cell
    r, c = -1, -1
    for i in range(rows):
        for j in range(cols):
            if grid[i][j] == EMPTY:
                r, c = i, j
                break
        if r != -1:
            break

    if r == -1:
        return grid  # solved

    for val in (FILLED, CROSS):
        new_grid = [row[:] for row in grid]
        new_grid[r][c] = val

        changed = True
        valid = True
        while changed and valid:
            changed = False
            for ri in range(rows):
                line = new_grid[ri]
                new_line = solve_line(row_clues[ri], line)
                if new_line is None:
                    valid = False
                    break
                if new_line != line:
                    new_grid[ri] = new_line
                    changed = True
            if not valid:
                break
            for ci in range(cols):
                line = [new_grid[ri][ci] for ri in range(rows)]
                new_line = solve_line(col_clues[ci], line)
                if new_line is None:
                    valid = False
                    break
                if new_line != line:
                    for ri in range(rows):
                        new_grid[ri][ci] = new_line[ri]
                    changed = True

        if valid:
            result = _backtrack(new_grid, row_clues, col_clues, rows, cols)
            if result is not None:
                return result

    return None


def print_grid(grid: list[list[int]]) -> None:
    for row in grid:
        print("".join("█" if c == FILLED else "x" if c == CROSS else "." for c in row))
