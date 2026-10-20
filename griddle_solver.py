"""Exact Griddle solving with maximal forward words and cell modifiers."""

import argparse
from collections import Counter
from collections.abc import Iterable
import csv
from dataclasses import dataclass
from itertools import product
import json
from pathlib import Path
import sys
import re
from typing import Any
import config

from ortools.sat.python import cp_model
from spellchecker import SpellChecker



VOWELS = frozenset("aeiouy")
TILE_MODIFIERS = {None, "DL", "TL", "DW", "TW"}

Grid = list[list[str | None]]


class InputError(ValueError):
    pass


class InvalidBoard(ValueError):
    pass

class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, complex):
            return [obj.real, obj.imag]
        # Let the base class default method raise the TypeError
        return super().default(obj)


@dataclass(frozen=True)
class Puzzle:
    letters: str
    modifiers: tuple[tuple[str | None, ...], ...]

    @property
    def inventory(self) -> Counter[str]:
        return Counter(self.letters)


def parse_puzzle(letters: str) -> Puzzle:
    if not isinstance(letters, str) or not 8 <= len(letters) <= 10:
        raise InputError("Supply 8-10 letter tiles.")
    if not letters.isascii() or not letters.isalpha():
        raise InputError("Tiles must be ASCII English letters without spaces or blanks.")
    modifiers = config.BOARD_MODIFIERS
    if modifiers is None:
        modifiers = [[None] * 4 for _ in range(4)]
    if not isinstance(modifiers, list) or len(modifiers) != 4:
        raise InputError("Modifier board must be a 4x4 JSON array.")
    rows = []
    for row_index, row in enumerate(modifiers):
        if not isinstance(row, list) or len(row) != 4:
            raise InputError("Modifier board must be a 4x4 JSON array.")
        for column_index, modifier in enumerate(row):
            if not (modifier is None or isinstance(modifier, str)) or modifier not in TILE_MODIFIERS:
                raise InputError(
                    f"Invalid modifier at [{row_index}, {column_index}]; "
                    "use null, DL, TL, DW, or TW, with one modifier per cell."
                )
        rows.append(tuple(row))
    return Puzzle(letters.lower(), tuple(rows))


def word_allowed(word: str, vocabulary: set[str]) -> bool:
    return (
        2 <= len(word) <= 4
        and word.isascii()
        and word.isalpha()
        and word in vocabulary
        and (bool(VOWELS.intersection(word)))
    )


def filter_vocabulary(words: Iterable[str], inventory: Counter[str]) -> set[str]:
    accepted = set()
    for entry in words:
        word = entry.lower()
        if (
            2 <= len(word) <= 4
            and word.isascii()
            and word.isalpha()
            and (bool(VOWELS.intersection(word)))
            and not Counter(word) - inventory
        ):
            accepted.add(word)
    return accepted


def load_vocabulary(puzzle: Puzzle) -> set[str]:
    dictionary = SpellChecker(language="en")
    return filter_vocabulary(dictionary.word_frequency.words(), puzzle.inventory)


def load_exclusions(path: Path) -> set[str]:
    excluded = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            for row in reader:
                for column, field in enumerate(row, start=1):
                    word = field.strip()
                    if not word:
                        continue
                    if not word.isascii() or not word.isalpha():
                        raise InputError(
                            f"Invalid excluded word in {path} at line {reader.line_num}, "
                            f"field {column}; use ASCII letters only."
                        )
                    excluded.add(word.lower())
    except (OSError, UnicodeError, csv.Error) as error:
        raise InputError(f"Cannot read exclusion CSV {path}: {error}") from error
    return excluded


def extract_runs(board: Grid):
    for direction in ("horizontal", "vertical"):
        for line_index in range(4):
            run = []
            for offset in range(5):
                coordinate = (line_index, offset) if direction == "horizontal" else (offset, line_index)
                if offset < 4 and board[coordinate[0]][coordinate[1]] is not None:
                    run.append(coordinate)
                else:
                    if len(run) >= 2:
                        yield direction, tuple(run)
                    run = []


def evaluate_board(puzzle: Puzzle, board: Grid, vocabulary: set[str]) -> dict[str, Any]:
    if len(board) != 4 or any(len(row) != 4 for row in board):
        raise InvalidBoard("Grid must be 4x4.")
    normalized: Grid = []
    for row in board:
        normalized_row = []
        for cell in row:
            if cell is not None and not (
                isinstance(cell, str) and len(cell) == 1 and cell.isascii() and cell.isalpha()
            ):
                raise InvalidBoard("Grid cells must be single letters or empty.")
            normalized_row.append(cell.lower() if cell is not None else None)
        normalized.append(normalized_row)
    used = Counter(cell for row in normalized for cell in row if cell is not None)
    if used - puzzle.inventory:
        raise InvalidBoard("Grid exceeds the supplied letter inventory.")
    occupied = {
        (row, column) for row in range(4) for column in range(4)
        if normalized[row][column] is not None
    }
    participating = set()
    words = []
    for direction, coordinates in extract_runs(normalized):
        spelling = "".join(normalized[row][column] or "" for row, column in coordinates)
        if not word_allowed(spelling, vocabulary):
            raise InvalidBoard(f"Invalid {direction} word: {spelling.upper()}.")
        letter_total = 0
        word_multiplier = 1
        for letter, (row, column) in zip(spelling, coordinates):
            modifier = puzzle.modifiers[row][column]
            letter_total += config.LETTER_VALUES[letter] * {"DL": 2, "TL": 3}.get(modifier, 1)
            word_multiplier *= {"DW": 2, "TW": 3}.get(modifier, 1)
        words.append({
            "spelling": spelling.upper(), "direction": direction,
            "coordinates": [list(coordinate) for coordinate in coordinates],
            "score": letter_total * word_multiplier,
        })
        participating.update(coordinates)
    if not words:
        raise InvalidBoard("Grid must contain at least one valid word.")
    if participating != occupied:
        raise InvalidBoard("Every placed tile must participate in a valid word.")
    visited = set()
    frontier = [next(iter(occupied))]
    while frontier:
        coordinate = frontier.pop()
        if coordinate in visited:
            continue
        visited.add(coordinate)
        row, column = coordinate
        for neighbor in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
            if neighbor in occupied and neighbor not in visited:
                frontier.append(neighbor)
    if visited != occupied:
        raise InvalidBoard("Placed tiles must be orthogonally connected.")
    return {
        "grid": [[cell.upper() if cell is not None else None for cell in row] for row in normalized],
        "total_score": sum(word["score"] for word in words),
        "unused_tiles": "".join(sorted((puzzle.inventory - used).elements())).upper(),
        "words": words,
    }


@dataclass(frozen=True)
class LinePattern:
    cells: tuple[int, ...]
    participation: tuple[int, ...]
    runs: tuple[tuple[int, int], ...]


@dataclass
class BoardModel:
    model: cp_model.CpModel
    cells: list[cp_model.IntVar]
    alphabet: tuple[str, ...]
    scores: list[cp_model.IntVar]


def line_patterns(puzzle: Puzzle, vocabulary: set[str], alphabet: tuple[str, ...]) -> list[LinePattern]:
    patterns = []
    inventory = puzzle.inventory
    for cells in product(range(len(alphabet)), repeat=4):
        counts = Counter(alphabet[value] for value in cells if value)
        if counts - inventory:
            continue
        participation = [0] * 4
        runs = []
        start = 0
        valid = True
        while start < 4:
            if cells[start] == 0:
                start += 1
                continue
            end = start + 1
            while end < 4 and cells[end] != 0:
                end += 1
            if end - start >= 2:
                word = "".join(alphabet[value] for value in cells[start:end])
                if not word_allowed(word, vocabulary):
                    valid = False
                    break
                participation[start:end] = [1] * (end - start)
                runs.append((start, end))
            start = end
        if valid:
            patterns.append(LinePattern(cells, tuple(participation), tuple(runs)))
    return patterns


def line_score(pattern: LinePattern, alphabet: tuple[str, ...], modifiers: tuple[str | None, ...]) -> int:
    total = 0
    for start, end in pattern.runs:
        subtotal = 0
        multiplier = 1
        for offset in range(start, end):
            value = config.LETTER_VALUES[alphabet[pattern.cells[offset]]]
            modifier = modifiers[offset]
            if modifier == "DL":
                value *= 2
            elif modifier == "TL":
                value *= 3
            elif modifier == "DW":
                multiplier *= 2
            elif modifier == "TW":
                multiplier *= 3
            subtotal += value
        total += subtotal * multiplier
    return total


def build_model(puzzle: Puzzle, vocabulary: set[str]) -> BoardModel:
    model = cp_model.CpModel()
    alphabet = ("", *sorted(puzzle.inventory))
    cells = [model.new_int_var(0, len(alphabet) - 1, f"cell_{index}") for index in range(16)]
    occupied = [model.new_bool_var(f"occupied_{index}") for index in range(16)]
    for cell, presence in zip(cells, occupied):
        model.add(cell != 0).only_enforce_if(presence)
        model.add(cell == 0).only_enforce_if(~presence)
    model.add(sum(occupied) >= 2)
    for letter_index, letter in enumerate(alphabet[1:], start=1):
        indicators = []
        for index, cell in enumerate(cells):
            indicator = model.new_bool_var(f"letter_{letter_index}_{index}")
            model.add(cell == letter_index).only_enforce_if(indicator)
            model.add(cell != letter_index).only_enforce_if(~indicator)
            indicators.append(indicator)
        model.add(sum(indicators) <= puzzle.inventory[letter])
    horizontal = [model.new_bool_var(f"horizontal_{index}") for index in range(16)]
    vertical = [model.new_bool_var(f"vertical_{index}") for index in range(16)]
    patterns = line_patterns(puzzle, vocabulary, alphabet)
    scores = []
    for direction, participation in (("horizontal", horizontal), ("vertical", vertical)):
        for line_index in range(4):
            indices = [line_index * 4 + offset if direction == "horizontal" else offset * 4 + line_index for offset in range(4)]
            modifiers = tuple(puzzle.modifiers[index // 4][index % 4] for index in indices)
            table = [(*pattern.cells, *pattern.participation, line_score(pattern, alphabet, modifiers)) for pattern in patterns]
            score = model.new_int_var(0, max(row[-1] for row in table), f"score_{direction}_{line_index}")
            model.add_allowed_assignments(
                [*(cells[index] for index in indices), *(participation[index] for index in indices), score], table
            )
            scores.append(score)
    for index in range(16):
        model.add(horizontal[index] + vertical[index] >= occupied[index])
    roots = [model.new_bool_var(f"root_{index}") for index in range(16)]
    injections = [model.new_int_var(0, len(puzzle.letters), f"source_{index}") for index in range(16)]
    model.add(sum(roots) == 1)
    model.add(sum(injections) == sum(occupied))
    for index in range(16):
        model.add(roots[index] <= occupied[index])
        model.add(injections[index] <= len(puzzle.letters) * roots[index])
    incoming: list[list[cp_model.IntVar]] = [[] for _ in range(16)]
    outgoing: list[list[cp_model.IntVar]] = [[] for _ in range(16)]
    for source in range(16):
        row, column = divmod(source, 4)
        for target_row, target_column in ((row - 1, column), (row + 1, column), (row, column - 1), (row, column + 1)):
            if not (0 <= target_row < 4 and 0 <= target_column < 4):
                continue
            target = target_row * 4 + target_column
            flow = model.new_int_var(0, len(puzzle.letters), f"flow_{source}_{target}")
            model.add(flow <= len(puzzle.letters) * occupied[source])
            model.add(flow <= len(puzzle.letters) * occupied[target])
            outgoing[source].append(flow)
            incoming[target].append(flow)
    for index in range(16):
        model.add(injections[index] + sum(incoming[index]) - sum(outgoing[index]) == occupied[index])
    model.maximize(sum(scores))
    return BoardModel(model, cells, alphabet, scores)


def solve_puzzle(
    puzzle: Puzzle, vocabulary: set[str] | None = None, *, omitted_words: set[str] | None = None
) -> dict[str, Any]:
    vocabulary = load_vocabulary(puzzle) if vocabulary is None else filter_vocabulary(vocabulary, puzzle.inventory)
    vocabulary = vocabulary - (omitted_words or set())
    board_model = build_model(puzzle, vocabulary)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8
    outcome = solver.solve(board_model.model)
    if outcome == cp_model.INFEASIBLE:
        return {"status": "no_solution", "message": "No legal arrangement exists."}
    if outcome in (cp_model.FEASIBLE, cp_model.UNKNOWN):
        return {"status": "inconclusive", "message": "Search did not establish optimality or infeasibility."}
    if outcome != cp_model.OPTIMAL:
        raise RuntimeError("Optimization model failed: " + solver.status_name(outcome))
    board = [
        [board_model.alphabet[solver.value(board_model.cells[row * 4 + column])] or None for column in range(4)]
        for row in range(4)
    ]
    try:
        result = evaluate_board(puzzle, board, vocabulary)
    except InvalidBoard as error:
        raise RuntimeError(f"Solver result failed independent legality audit: {error}") from error
    model_score = sum(solver.value(score) for score in board_model.scores)
    if result["total_score"] != model_score or result["total_score"] != solver.objective_value:
        raise RuntimeError("Solver result failed independent score audit.")
    return {"status": "optimal", **result}


class PuzzleArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def render_result(result: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        with open("output.json", "w", encoding="utf-8") as file:
            json.dump(result, file, indent=4)
        with open("output.json", "r", encoding="utf-8") as file:
            content = file.read()
        content = re.sub(
            r'\[\s*((?:"[^"]*"|null|true|false|-?\d+(?:\.\d+)?)(?:\s*,\s*(?:"[^"]*"|null|true|false|-?\d+(?:\.\d+)?))*)\s*\]',
            lambda m: "[" + re.sub(r"\s+", " ", m.group(1)).strip() + "]",
            content
        )
        with open("output.json", "w", encoding="utf-8") as file:
            file.write(content)
        return
    
    if result["status"] != "optimal":
        print(f"{result['status']}: {result['message']}")
        return
    print("Status: optimal")
    for row in result["grid"]:
        print(" ".join(cell or "." for cell in row))
    print(f"Total score: {result['total_score']}")
    print(f"Unused tiles: {result['unused_tiles'] or '(none)'}")
    for word in result["words"]:
        print(f"{word['spelling']} {word['direction']} {word['coordinates']}: {word['score']}")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    json_mode = "--json" in arguments
    parser = PuzzleArgumentParser(description="Find a proven maximum-score legal Griddle layout.", allow_abbrev=False)
    parser.add_argument("--letters", required=True, help="8-10 ASCII letter tiles, without spaces")
    parser.add_argument("--omit", type=Path, default="exclude.csv", help="[Optional] Headerless CSV file of words to exclude")
    parser.add_argument("--json", action="store_true", help="[Optional] Emit structured results and errors as a JSON file")
    try:
        options = parser.parse_args(arguments)
        puzzle = parse_puzzle(options.letters)
        omitted_words = load_exclusions(options.omit) if options.omit is not None else None
        result = solve_puzzle(puzzle, omitted_words=omitted_words)
        exit_code = {"optimal": 0, "no_solution": 1, "inconclusive": 3}[result["status"]]
    except InputError as error:
        result = {"status": "input_error", "message": str(error)}
        exit_code = 2
    except KeyboardInterrupt:
        result = {"status": "interrupted", "message": "Search interrupted; no optimum or infeasibility proof is claimed."}
        exit_code = 130
    except Exception as error:
        result = {"status": "internal_error", "message": str(error)}
        exit_code = 4
    render_result(result, json_mode)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())