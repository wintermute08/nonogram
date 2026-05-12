"""
Nonogram solver bot for nemonemologic.com

Usage:
    python main.py <puzzle_id>           # solve puzzle
    python main.py --inspect <puzzle_id> # inspect DOM without solving
    python main.py --headless <puzzle_id># run headlessly
    python main.py --test                # run solver unit tests

Examples:
    python main.py 20278
    python main.py --inspect 20278
    python main.py --headless 20278
"""

import asyncio
import sys

from bot import NonogramBot, inspect_puzzle
from solver import solve, print_grid


def run_tests():
    """Quick sanity check of the solver."""
    print("=== Solver tests ===")

    # 5x5 puzzle
    row_clues = [[2], [1, 1], [3], [1, 1], [2]]
    col_clues = [[2], [1, 1], [3], [1, 1], [2]]
    grid = solve(row_clues, col_clues)
    assert grid is not None, "Should be solvable"
    print("Test 1 passed:")
    print_grid(grid)

    # Known 5x5
    row_clues = [[1, 1], [5], [1, 1], [1, 1], [1, 1]]
    col_clues = [[5], [1], [5], [1], [5]]
    grid = solve(row_clues, col_clues)
    assert grid is not None
    print("\nTest 2 passed:")
    print_grid(grid)

    print("\nAll tests passed!")


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "--test" in args:
        run_tests()
        return

    headless = "--headless" in args
    inspect = "--inspect" in args
    args = [a for a in args if not a.startswith("--")]

    if not args:
        print("Error: puzzle_id required")
        print(__doc__)
        sys.exit(1)

    try:
        puzzle_id = int(args[0])
    except ValueError:
        print(f"Error: invalid puzzle_id '{args[0]}'")
        sys.exit(1)

    if inspect:
        asyncio.run(inspect_puzzle(puzzle_id))
    else:
        bot = NonogramBot(headless=headless, slow_mo=80)
        asyncio.run(bot.run(puzzle_id))


if __name__ == "__main__":
    main()
