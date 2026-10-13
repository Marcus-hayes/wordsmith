# Wordsmith Griddle Solver

An exact solver for a 4x4 Griddle board using 8-10 supplied letter tiles.

## Setup

Tested on macOS with Python 3.14.7, PySpellchecker 0.9.1, and OR-Tools 9.15.6755.
Use a Python interpreter for which the pinned dependencies have compatible wheels.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

Run tests from the repository root:

```sh
python -m unittest discover -s tests
```

## Usage

From the repository root with the virtual environment active:

```sh
python griddle_solver.py --letters ABXXXXXX
python griddle_solver.py --letters ABXXXXXX --json
python griddle_solver.py --letters ABXXXXXX --modifiers examples/modifiers.json --json
python griddle_solver.py --help
```

Replace the sample inventory with your daily tiles. The modifier example below is
also provided as `examples/modifiers.json` for the third command.

Readable output shows a 4x4 grid with `.` for empty cells, total score, unused
tiles, and each word's spelling, direction, zero-based coordinates, and score.
JSON uses `null` for empty cells and has `status`, `grid`, `total_score`,
`unused_tiles`, and `words` fields. Each word has `spelling`, `direction`,
`coordinates`, and `score`; word scores sum to the total.

Failures contain `status` and `message`, including when `--json` is used.
Exit statuses:

| Code | Status | Meaning |
| --- | --- | --- |
| 0 | `optimal` | Audited, proven maximum-score layout |
| 1 | `no_solution` | Proven absence of a legal layout |
| 2 | `input_error` | Invalid arguments, inventory, modifier file, or exclusion CSV |
| 3 | `inconclusive` | Neither optimality nor infeasibility established |
| 4 | `internal_error` | Model, dependency, or independent audit failure |
| 130 | `interrupted` | User interruption; no proof claimed |

Use Ctrl+C to interrupt a long-running search. It does not return a claimed
optimal grid. Native solving may take time to yield control to the interrupt.

## Exclusion CSV Format

Use optional `--omit <csv-path>` to exclude words before solving. From the
repository root with the virtual environment active:

```sh
python griddle_solver.py --letters ABXXXXXX --omit examples/exclude.csv
python griddle_solver.py --letters ABXXXXXX --modifiers examples/modifiers.json --omit examples/exclude.csv --json
```

The project-local example excludes DUNN. To use your separate user-managed file,
pass its path explicitly:

```sh
python griddle_solver.py --letters ABCXXXXX --modifiers examples/modifiers.json --omit examples/exclude.csv
```

Relative paths are resolved from the command's working directory; absolute paths
also work. The argument is a file path, not an inline comma-separated list.
No exclusion file is read unless `--omit` is supplied, even if an `exclude.csv`
is present in the working directory.

Word exclusion files use UTF-8 CSV with no header. Every cell is a word, so
comma-separated words and one word per row can be mixed:

```csv
mud,wind
din
```

Standard quoted fields and an optional leading UTF-8 byte-order mark are
supported. Surrounding whitespace is trimmed, letter case is ignored, duplicates
are collapsed, and blank rows and empty fields are ignored. For example,
`MUD," wind ",mud` excludes only MUD and WIND. An empty file excludes nothing.

Nonempty fields must contain only ASCII letters. Words absent from the dictionary,
longer than four letters, or unavailable from the current tiles are harmless.
There is no header detection: a first row containing `word` is itself an entry.
Unreadable files, invalid UTF-8, malformed CSV, or fields containing digits,
punctuation, or internal spaces are input errors; no partial blacklist is used.
Exclusion files and the bundled dictionary are never modified.

Exclusions match complete maximal words, not substrings: excluding AT still
permits a valid maximal CATS run. An excluded word makes the whole candidate
layout illegal; it is not merely assigned zero points. The solver searches for
the best remaining legal layout with exclusions enforced before optimization,
and the independent audit uses the same restricted vocabulary. If no legal
layout remains, the result is proven no solution. Scoring and result formats are
unchanged, and absent or empty exclusions preserve the existing legal layouts
and optimal score.

## Puzzle Rules

Supply 8-10 ASCII letters without spaces; case is ignored and duplicates are
individual tiles. Tiles may remain unused, but cannot be used more often than
supplied. Every placed tile must belong to a valid word, and all placed tiles
must form one horizontally or vertically connected arrangement.

Words are maximal uninterrupted runs of 2-4 letters read left-to-right or
top-to-bottom. Empty cells and board edges end a run. Every run must be valid;
shorter words embedded in a run do not score separately. Single-cell runs do not
score, but a tile can participate in a word in the other direction. Reverse and
diagonal readings never count. Separate occurrences of the same word each score.

Vocabulary is exact membership in PySpellchecker's pinned English dictionary,
not spelling suggestions and not necessarily the official Griddle vocabulary.
Two-letter words must contain A, E, I, O, U, or Y; Y is treated as a vowel.

The optional modifier file is a JSON array of four rows with four cells each.
Each cell is `null`, `DL`, `TL`, `DW`, or `TW`; combinations are rejected.
Coordinates are zero-based `[row, column]`. For example:

```json
[
	["DL", "DW", null, null],
	[null, null, null, null],
	[null, null, null, null],
	[null, null, null, "TW"]
]
```

Scores use standard English Scrabble values. Apply letter modifiers, sum the
letters, then multiply all word modifiers on that word. With C on DL and A on
DW, CAT scores `(3 * 2 + 1 + 1) * 2 = 16`. A crossing tile uses one inventory
tile but contributes to both words; its modifier applies to both. Empty modifier
cells have no effect. There are no tile-count or word-count bonuses.

## Optimality

OR-Tools CP-SAT searches the complete modeled set of legal layouts, including
layouts with unused tiles. Success means the solver proved global optimality
and an independent board evaluator verified legality and the reported score.
Any tied maximum is acceptable. Proven infeasibility is reported as no solution.
An incomplete search is inconclusive, never a claimed optimum or no-solution proof.

There is no default time limit. Exact solving can be slow for some puzzles;
interrupting a search does not establish optimality. Tests compare controlled
puzzles against an independent exhaustive oracle, including asymmetric modifiers.

## Verification

The test suite covers puzzle validation, exact dictionary membership, maximal
word runs, tile multiplicities, connectivity, modifier scoring, solver statuses,
independent result auditing, exhaustive optimality comparisons, and CLI output.

Observed on the tested macOS/Python environment using eight CP-SAT workers:

| Tiles | Inventory | Modifiers | Proven Score | Solve Seconds |
| --- | --- | --- | --- | --- |
| 8 | `CATBYMYX` | None | 39 | 3.122 |
| 9 | `CATBYMYXS` | None | 45 | 5.715 |
| 10 | `CATBYMYXSE` | `examples/modifiers.json` | 118 | 13.063 |

Times include dictionary loading, model construction, optimization, and the
solver's result audit. Each result was also checked by a separate evaluator
invocation. These are observations, not runtime guarantees; other puzzles or
machines can take substantially longer. Tied optimal grids can differ by run.
