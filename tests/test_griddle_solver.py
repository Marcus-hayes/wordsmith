from collections import Counter
from contextlib import redirect_stdout
import csv
import io
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import griddle_solver as griddle
from ortools.sat.python import cp_model


def empty_modifiers():
    return [[None] * 4 for _ in range(4)]


def grid(*rows):
    return [[None if letter == "." else letter for letter in row] for row in rows]


def exhaustive_ab_maximum(modifiers):
    maximum = 0
    lines = [list(range(row * 4, row * 4 + 4)) for row in range(4)]
    lines += [list(range(column, 16, 4)) for column in range(4)]
    for size in range(2, 7):
        for positions in combinations(range(16), size):
            occupied = set(positions)
            visited = {positions[0]}
            frontier = [positions[0]]
            while frontier:
                position = frontier.pop()
                row, column = divmod(position, 4)
                for neighbor in occupied - visited:
                    neighbor_row, neighbor_column = divmod(neighbor, 4)
                    if abs(row - neighbor_row) + abs(column - neighbor_column) == 1:
                        visited.add(neighbor)
                        frontier.append(neighbor)
            if visited != occupied:
                continue
            runs = []
            covered = set()
            invalid = False
            for line in lines:
                current = []
                for position in [*line, -1]:
                    if position in occupied:
                        current.append(position)
                    else:
                        if len(current) >= 2:
                            if len(current) != 2:
                                invalid = True
                                break
                            runs.append(tuple(current))
                            covered.update(current)
                        current = []
                if invalid:
                    break
            if invalid or covered != occupied:
                continue
            for a_count in (1, 2):
                for a_positions in combinations(positions, a_count):
                    a_cells = set(a_positions)
                    if any(first not in a_cells or second in a_cells for first, second in runs):
                        continue
                    score = 0
                    for first, second in runs:
                        first_modifier = modifiers[first // 4][first % 4]
                        second_modifier = modifiers[second // 4][second % 4]
                        letter_total = {"DL": 2, "TL": 3}.get(first_modifier, 1)
                        letter_total += 3 * {"DL": 2, "TL": 3}.get(second_modifier, 1)
                        score += letter_total * {"DW": 2, "TW": 3}.get(first_modifier, 1) * {"DW": 2, "TW": 3}.get(second_modifier, 1)
                    maximum = max(maximum, score)
    return maximum


class InputTests(unittest.TestCase):
    def test_normalization_and_defaults(self):
        for letters in ("aAbBcCdD", "aabbbccdd", "aabbccddee"):
            with self.subTest(letters=letters):
                puzzle = griddle.parse_puzzle(letters)
                self.assertEqual(puzzle.inventory, Counter(letters.lower()))
                self.assertEqual(puzzle.modifiers, tuple((None,) * 4 for _ in range(4)))

    def test_invalid_inventory(self):
        for letters in ("abcdefg", "abcdefghijk", "abcd1234", "abcd efgh", "abcdéfgh", "abc?defg", None):
            with self.subTest(letters=letters), self.assertRaises(griddle.InputError):
                griddle.parse_puzzle(letters)

    def test_modifiers(self):
        board = empty_modifiers()
        board[0] = ["DL", "TL", "DW", "TW"]
        self.assertEqual(griddle.parse_puzzle("aabbccdd", board).modifiers[0], tuple(board[0]))

    def test_invalid_modifier_board(self):
        boards = [[], {}, [[None] * 4] * 3, [[None] * 3] * 4]
        for modifier in ("4L", "dl", ["DL", "DW"], {"DL": 2}, 2, False):
            board = empty_modifiers()
            board[0][0] = modifier
            boards.append(board)
        for board in boards:
            with self.subTest(board=board), self.assertRaises(griddle.InputError):
                griddle.parse_puzzle("aabbccdd", board)


class VocabularyTests(unittest.TestCase):
    def test_controlled_filter(self):
        words = {"AT", "by", "my", "sh", "hm", "cat", "cats", "a", "longer", "café", "a-t", "aa", "zz"}
        result = griddle.filter_vocabulary(words, Counter("atbymcsh"))
        self.assertEqual(result, {"at", "by", "my", "cat", "cats"})

    def test_exact_membership_and_vowel_filter(self):
        vocabulary = {"cat", "sh", "by", "my", "a", "longer"}
        for word in ("cat", "by", "my"):
            self.assertTrue(griddle.word_allowed(word, vocabulary))
        for word in ("cta", "sh", "a", "longer"):
            self.assertFalse(griddle.word_allowed(word, vocabulary))

    def test_real_dictionary_without_corrections(self):
        puzzle = griddle.parse_puzzle("catbymyx")
        with patch.object(griddle.SpellChecker, "correction", side_effect=AssertionError("No corrections")):
            vocabulary = griddle.load_vocabulary(puzzle)
        self.assertTrue({"cat", "by", "my"} <= vocabulary)
        self.assertNotIn("cta", vocabulary)
        self.assertNotIn("sh", griddle.load_vocabulary(griddle.parse_puzzle("shaaaaaa")))


class ExclusionLoadingTests(unittest.TestCase):
    def test_headerless_csv_normalization(self):
        fixtures = (
            (b"mud,wind\ndin\n", {"mud", "wind", "din"}),
            (b'MUD," wind ",mud\n\n, ,\n', {"mud", "wind"}),
            (b"\xef\xbb\xbfmud,wind\r\n", {"mud", "wind"}),
            (b'"mud",wind\r\ndin\r\n', {"mud", "wind", "din"}),
            (b"word\nmud\n", {"word", "mud"}),
            (b"zzzz,unavailable,a\n", {"zzzz", "unavailable", "a"}),
            (b"", set()),
            (b"\n, ,\n\r\n", set()),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclude.csv"
            for contents, expected in fixtures:
                with self.subTest(contents=contents):
                    path.write_bytes(contents)
                    self.assertEqual(griddle.load_exclusions(path), expected)
                    self.assertEqual(path.read_bytes(), contents)

    def test_invalid_csv_rejects_partial_results(self):
        fixtures = (
            b"\xffmud", b'mud\n"wind', b'mud\n"wind"tail\n',
            b"mud\nm3d\n", b"mud\ntwo words\n", b"mud\nwind!\n",
            b"mud\ncaf\xc3\xa9\n", b'mud\n"wind,din"\n',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclude.csv"
            for contents in fixtures:
                with self.subTest(contents=contents):
                    path.write_bytes(contents)
                    with self.assertRaises(griddle.InputError) as failure:
                        griddle.load_exclusions(path)
                    self.assertIn(str(path), str(failure.exception))
                    self.assertEqual(path.read_bytes(), contents)

    def test_missing_unreadable_and_directory_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.csv"
            for target in (path, Path(directory)):
                with self.subTest(target=target), self.assertRaises(griddle.InputError):
                    griddle.load_exclusions(target)
            with patch.object(Path, "open", side_effect=PermissionError("Permission denied")):
                with self.assertRaisesRegex(griddle.InputError, "Permission denied"):
                    griddle.load_exclusions(path)


class EvaluationTests(unittest.TestCase):
    def evaluate(self, board, vocabulary, modifiers=None, letters="catsanex"):
        return griddle.evaluate_board(griddle.parse_puzzle(letters, modifiers), board, vocabulary)

    def test_maximal_word_lengths(self):
        for spelling in ("at", "cat", "cats"):
            board = grid(spelling.ljust(4, "."), "....", "....", "....")
            result = self.evaluate(board, {"at", "cat", "cats"})
            self.assertEqual([word["spelling"] for word in result["words"]], [spelling.upper()])
            self.assertEqual(result["words"][0]["direction"], "horizontal")
            self.assertEqual(result["words"][0]["coordinates"], [[0, column] for column in range(len(spelling))])

    def test_singletons_can_participate_vertically(self):
        result = self.evaluate(grid("a...", "t...", "....", "...."), {"at"})
        self.assertEqual(result["words"][0]["direction"], "vertical")

    def test_crossing_and_unused_inventory(self):
        modifiers = empty_modifiers()
        modifiers[0][0] = "DL"
        result = self.evaluate(grid("at..", "n...", "....", "...."), {"at", "an"}, modifiers)
        self.assertEqual(result["total_score"], 6)
        self.assertEqual([word["score"] for word in result["words"]], [3, 3])
        self.assertEqual(result["unused_tiles"], "ACESX")

    def test_repeated_words_count_per_occurrence(self):
        result = self.evaluate(grid("at..", ".at.", "....", "...."), {"at", "ta"}, letters="aattxxxx")
        self.assertEqual([word["spelling"] for word in result["words"]].count("AT"), 2)
        self.assertEqual(result["total_score"], 6)

    def test_invalid_layouts(self):
        boards = (
            grid("....", "....", "....", "...."),
            grid("a...", "....", "....", "...."),
            grid("at.e", "....", "....", "...."),
            grid("ta..", "....", "....", "...."),
            grid("t...", "a...", "....", "...."),
            grid("a...", ".t..", "....", "...."),
            grid("at..", "....", "....", "at.."),
            grid("at..", "..at", "....", "...."),
            grid("at..", "at..", "....", "...."),
        )
        for board in boards:
            with self.subTest(board=board), self.assertRaises(griddle.InvalidBoard):
                self.evaluate(board, {"at"}, letters="aattxxxx")

    def test_inventory_and_cell_validation(self):
        for board in (grid("aa..", "....", "....", "...."), [[None] * 3] * 4,
                      [["bad"] * 4] * 4, [["é"] * 4] * 4):
            with self.subTest(board=board), self.assertRaises(griddle.InvalidBoard):
                self.evaluate(board, {"aa"}, letters="abcdefgh")

    def test_all_scrabble_values(self):
        expected = dict(zip("abcdefghijklmnopqrstuvwxyz", (1, 3, 3, 2, 1, 4, 2, 4, 1, 8, 5, 1, 3, 1, 1, 3, 10, 1, 1, 1, 1, 4, 4, 8, 4, 10)))
        self.assertEqual(griddle.LETTER_VALUES, expected)

    def test_modifier_scores(self):
        for modifiers_row, expected in (
            ([None, None, None, None], 5),
            (["DL", "DW", None, None], 16),
            (["TL", None, None, None], 11),
            (["DW", "TW", None, None], 30),
            (["TW", None, None, None], 15),
            ([None, None, None, "TW"], 5),
        ):
            modifiers = empty_modifiers()
            modifiers[0] = modifiers_row
            with self.subTest(modifiers=modifiers):
                result = self.evaluate(grid("cat.", "....", "....", "...."), {"cat"}, modifiers)
                self.assertEqual(result["total_score"], expected)


class ModelTests(unittest.TestCase):
    def test_matches_exhaustive_oracle(self):
        asymmetric = empty_modifiers()
        asymmetric[0][1] = "TW"
        asymmetric[2][0] = "DL"
        asymmetric[3][3] = "DW"
        for modifiers in (empty_modifiers(), asymmetric):
            with self.subTest(modifiers=modifiers):
                expected = exhaustive_ab_maximum(modifiers)
                result = griddle.solve_puzzle(griddle.parse_puzzle("aabbbbbb", modifiers), {"ab"})
                self.assertEqual(result["status"], "optimal")
                self.assertEqual(result["total_score"], expected)

    def test_line_patterns_and_independent_scores(self):
        puzzle = griddle.parse_puzzle("catsanex")
        alphabet = ("", *sorted(puzzle.inventory))
        patterns = griddle.line_patterns(puzzle, {"at", "cat", "cats"}, alphabet)
        encoded = {"".join(alphabet[value] or "." for value in pattern.cells): pattern for pattern in patterns}
        self.assertIn("....", encoded)
        self.assertEqual(encoded["a..."].participation, (0, 0, 0, 0))
        self.assertEqual(encoded["at.e"].participation, (1, 1, 0, 0))
        self.assertNotIn("ta..", encoded)
        self.assertNotIn("aaaa", encoded)
        self.assertEqual(encoded["cats"].runs, ((0, 4),))
        for pattern in patterns:
            if not pattern.runs or any(value and not flag for value, flag in zip(pattern.cells, pattern.participation)):
                continue
            modifiers = empty_modifiers()
            modifiers[0] = ["DL", "DW", None, "TW"]
            board = [[alphabet[value] or None for value in pattern.cells], *[[None] * 4 for _ in range(3)]]
            result = griddle.evaluate_board(griddle.parse_puzzle(puzzle.letters, modifiers), board, {"at", "cat", "cats"})
            self.assertEqual(griddle.line_score(pattern, alphabet, tuple(modifiers[0])), result["total_score"])

    def test_crossing_counts_one_tile(self):
        result = griddle.solve_puzzle(griddle.parse_puzzle("abxxxxxx"), {"ab"})
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["total_score"], 4)
        result = griddle.solve_puzzle(griddle.parse_puzzle("abbxxxxx"), {"ab"})
        self.assertEqual(result["total_score"], 8)
        self.assertEqual(len(result["words"]), 2)
        self.assertEqual(result["unused_tiles"], "XXXXX")

    def test_forced_layout_constraints(self):
        puzzle = griddle.parse_puzzle("aattxxxx")
        cases = (
            (grid("at..", ".at.", "....", "...."), {"at", "ta"}, True),
            (grid("at..", "....", "....", "at.."), {"at"}, False),
            (grid("at..", "..at", "....", "...."), {"at"}, False),
            (grid("at..", "at..", "....", "...."), {"at"}, False),
            (grid("at.x", "....", "....", "...."), {"at"}, False),
            (grid("aaat", "....", "....", "...."), {"aaat"}, False),
        )
        for board, vocabulary, expected in cases:
            with self.subTest(board=board):
                built = griddle.build_model(puzzle, vocabulary)
                for index, cell in enumerate(cell for row in board for cell in row):
                    built.model.add(built.cells[index] == built.alphabet.index(cell or ""))
                outcome = cp_model.CpSolver().solve(built.model)
                self.assertEqual(outcome == cp_model.OPTIMAL, expected)
                if not expected:
                    self.assertEqual(outcome, cp_model.INFEASIBLE)

    def test_no_words_is_infeasible(self):
        result = griddle.solve_puzzle(griddle.parse_puzzle("aabbbbbb"), set())
        self.assertEqual(result["status"], "no_solution")

    def test_solver_statuses(self):
        for outcome in (cp_model.FEASIBLE, cp_model.UNKNOWN, cp_model.INFEASIBLE):
            with self.subTest(outcome=outcome), patch.object(cp_model.CpSolver, "solve", return_value=outcome):
                result = griddle.solve_puzzle(griddle.parse_puzzle("aabbbbbb"), {"ab"})
                self.assertEqual(result["status"], "no_solution" if outcome == cp_model.INFEASIBLE else "inconclusive")
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.MODEL_INVALID), self.assertRaises(RuntimeError):
            griddle.solve_puzzle(griddle.parse_puzzle("aabbbbbb"), {"ab"})

    def test_audit_failure(self):
        for failure in (griddle.InvalidBoard("Injected illegal grid"), {"total_score": -1}):
            arguments = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with self.subTest(failure=failure), patch.object(griddle, "evaluate_board", **arguments), self.assertRaises(RuntimeError):
                griddle.solve_puzzle(griddle.parse_puzzle("abxxxxxx"), {"ab"})


class ExclusionModelTests(unittest.TestCase):
    def test_best_remaining_layout_and_infeasibility(self):
        puzzle = griddle.parse_puzzle("abxxxxxx")
        vocabulary = {"ab", "ax"}
        unrestricted = griddle.solve_puzzle(puzzle, vocabulary)
        restricted = griddle.solve_puzzle(puzzle, vocabulary, omitted_words={"ax"})
        self.assertEqual(unrestricted["total_score"], 18)
        self.assertEqual(restricted["status"], "optimal")
        self.assertEqual(restricted["total_score"], 4)
        self.assertEqual([word["spelling"] for word in restricted["words"]], ["AB"])
        self.assertEqual(griddle.solve_puzzle(puzzle, vocabulary, omitted_words=vocabulary)["status"], "no_solution")

    def test_restricted_modified_maximum(self):
        modifiers = empty_modifiers()
        modifiers[0][0] = "TW"
        modifiers[0][1] = "DL"
        modifiers[2][2] = "DW"
        maximum = 0
        for row in range(4):
            for column in range(4):
                for next_row, next_column in ((row, column + 1), (row + 1, column)):
                    if next_row >= 4 or next_column >= 4:
                        continue
                    first = modifiers[row][column]
                    second = modifiers[next_row][next_column]
                    letter_score = {"DL": 2, "TL": 3}.get(first, 1) + 3 * {"DL": 2, "TL": 3}.get(second, 1)
                    score = letter_score * {"DW": 2, "TW": 3}.get(first, 1) * {"DW": 2, "TW": 3}.get(second, 1)
                    maximum = max(maximum, score)
        self.assertEqual(maximum, 21)
        result = griddle.solve_puzzle(griddle.parse_puzzle("abxxxxxx", modifiers), {"ab", "ax"}, omitted_words={"ax"})
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["total_score"], maximum)

    def test_shared_vocabulary_and_no_set_mutation(self):
        puzzle = griddle.parse_puzzle("abxxxxxx")
        vocabulary = {"ab", "ax"}
        omissions = {"ax"}
        for supplied_vocabulary in (None, vocabulary):
            with self.subTest(default_dictionary=supplied_vocabulary is None):
                with patch.object(griddle, "load_vocabulary", return_value=vocabulary), \
                     patch.object(griddle, "build_model", wraps=griddle.build_model) as building, \
                     patch.object(griddle, "evaluate_board", wraps=griddle.evaluate_board) as auditing:
                    result = griddle.solve_puzzle(puzzle, supplied_vocabulary, omitted_words=omissions)
                self.assertEqual(result["status"], "optimal")
                self.assertEqual(building.call_args.args[1], {"ab"})
                self.assertIs(building.call_args.args[1], auditing.call_args.args[2])
                self.assertEqual(vocabulary, {"ab", "ax"})
                self.assertEqual(omissions, {"ax"})

    def test_no_exclusions_preserve_vocabulary_and_score(self):
        puzzle = griddle.parse_puzzle("abxxxxxx")
        vocabulary = {"ab", "ax"}
        for omissions in (None, set(), {"unavailable"}):
            with self.subTest(omissions=omissions), patch.object(griddle, "build_model", wraps=griddle.build_model) as building:
                result = griddle.solve_puzzle(puzzle, vocabulary, omitted_words=omissions)
            self.assertEqual(building.call_args.args[1], vocabulary)
            self.assertEqual(result["total_score"], 18)

    def test_excluded_runs_rejected_by_model_and_evaluator(self):
        cases = (
            ("horizontal", "mudxxxxx", grid("mud.", "....", "....", "...."), {"mud"}, "mud"),
            ("vertical", "mudxxxxx", grid("m...", "u...", "d...", "...."), {"mud"}, "mud"),
            ("repeated", "mudmudxx", grid("mud.", ".mud", "....", "...."), {"mud", "um", "du"}, "mud"),
            ("incidental", "mudmudxx", grid("mud.", ".mud", "....", "...."), {"mud", "um", "du"}, "du"),
            ("crossing", "mudmxxxx", grid("mud.", ".m..", "....", "...."), {"mud", "um"}, "um"),
        )
        for name, letters, board, vocabulary, omission in cases:
            with self.subTest(name=name):
                puzzle = griddle.parse_puzzle(letters)
                self.assertGreater(griddle.evaluate_board(puzzle, board, vocabulary)["total_score"], 0)
                for allowed, expected in ((vocabulary, cp_model.OPTIMAL), (vocabulary - {omission}, cp_model.INFEASIBLE)):
                    built = griddle.build_model(puzzle, allowed)
                    for index, cell in enumerate(cell for row in board for cell in row):
                        built.model.add(built.cells[index] == built.alphabet.index(cell or ""))
                    self.assertEqual(cp_model.CpSolver().solve(built.model), expected)
                with self.assertRaises(griddle.InvalidBoard):
                    griddle.evaluate_board(puzzle, board, vocabulary - {omission})

    def test_substrings_do_not_exclude_longer_words(self):
        puzzle = griddle.parse_puzzle("catsxxxx")
        result = griddle.solve_puzzle(puzzle, {"cats", "cat", "at"}, omitted_words={"at"})
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["total_score"], 6)
        self.assertEqual([word["spelling"] for word in result["words"]], ["CATS"])

    def test_injected_excluded_word_fails_audit(self):
        original_evaluator = griddle.evaluate_board
        injected_board = grid("ax..", "x...", "....", "....")

        def injected_audit(puzzle, board, vocabulary):
            return original_evaluator(puzzle, injected_board, vocabulary)

        with patch.object(griddle, "evaluate_board", side_effect=injected_audit):
            with self.assertRaisesRegex(RuntimeError, "legality audit"):
                griddle.solve_puzzle(griddle.parse_puzzle("abxxxxxx"), {"ab", "ax"}, omitted_words={"ax"})


class CliTests(unittest.TestCase):
    def invoke(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = griddle.main(arguments)
        return exit_code, output.getvalue()

    def test_json_and_readable_success(self):
        result = griddle.solve_puzzle(griddle.parse_puzzle("abbxxxxx"), {"ab"})
        for json_mode in (True, False):
            with patch.object(griddle, "solve_puzzle", return_value=result):
                code, output = self.invoke(["--letters", "abbxxxxx", *(["--json"] if json_mode else [])])
            self.assertEqual(code, 0)
            if json_mode:
                payload = json.loads(output)
                self.assertEqual(payload["status"], "optimal")
                self.assertEqual(len(payload["grid"]), 4)
                self.assertTrue(all(len(row) == 4 for row in payload["grid"]))
                self.assertIn(None, [cell for row in payload["grid"] for cell in row])
                self.assertEqual(payload["total_score"], sum(word["score"] for word in payload["words"]))
                self.assertTrue(all(0 <= coordinate < 4 for word in payload["words"] for position in word["coordinates"] for coordinate in position))
            else:
                for text in ("Status: optimal", "Total score: 8", "Unused tiles: XXXXX", "AB", "."):
                    self.assertIn(text, output)

    def test_failure_statuses(self):
        outcomes = (
            ({"status": "no_solution", "message": "No legal arrangement exists."}, 1, "no_solution"),
            ({"status": "inconclusive", "message": "No proof."}, 3, "inconclusive"),
            (RuntimeError("Model failure"), 4, "internal_error"),
            (KeyboardInterrupt(), 130, "interrupted"),
        )
        for outcome, expected_code, status in outcomes:
            arguments = {"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}
            for json_mode in (True, False):
                with self.subTest(status=status, json=json_mode), patch.object(griddle, "solve_puzzle", **arguments):
                    code, output = self.invoke(["--letters", "aabbbbbb", *(["--json"] if json_mode else [])])
                self.assertEqual(code, expected_code)
                self.assertEqual(json.loads(output)["status"], status) if json_mode else self.assertIn(status, output)

    def test_input_errors_do_not_solve(self):
        for arguments in (
            ["--json"], ["--letters", "short", "--json"],
            ["--letters", "aabbbbbb", "--unknown", "--json"],
            ["--letters", "aabbbbbb", "--modifiers", "/missing/wordsmith-modifiers.json", "--json"],
        ):
            with self.subTest(arguments=arguments), patch.object(griddle, "solve_puzzle") as solving:
                code, output = self.invoke(arguments)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output)["status"], "input_error")
                solving.assert_not_called()

    def test_modifier_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modifiers.json"
            for contents in ("invalid JSON", "null", "[]", '["DL"]'):
                path.write_text(contents)
                code, output = self.invoke(["--letters", "aabbbbbb", "--modifiers", str(path), "--json"])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(output)["status"], "input_error")
            board = empty_modifiers()
            board[0][0] = "DL"
            path.write_text(json.dumps(board))
            with patch.object(griddle, "solve_puzzle", return_value={"status": "no_solution", "message": "No words"}) as solving:
                code, _ = self.invoke(["--letters", "aabbbbbb", "--modifiers", str(path), "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(solving.call_args.args[0].modifiers[0][0], "DL")

    def test_real_dictionary_subprocess(self):
        root = Path(__file__).resolve().parents[1]
        process = subprocess.run(
            [sys.executable, str(root / "griddle_solver.py"), "--letters", "ABXXXXXX", "--json"],
            capture_output=True, text=True, cwd=root, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        self.assertEqual(process.stderr, "")
        payload = json.loads(process.stdout)
        self.assertEqual(payload["status"], "optimal")
        puzzle = griddle.parse_puzzle("ABXXXXXX")
        audited = griddle.evaluate_board(puzzle, payload["grid"], griddle.load_vocabulary(puzzle))
        self.assertEqual(audited["total_score"], payload["total_score"])

    def test_help(self):
        with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as stopped:
            griddle.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        for flag in ("--letters", "--modifiers", "--omit", "--json"):
            self.assertIn(flag, output.getvalue())

    def test_exclusion_input_errors_prevent_solving(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclude.csv"
            path.write_text("mud\nm3d\n", encoding="utf-8")
            missing = Path(directory) / "missing.csv"
            fixtures = (
                ["--letters", "abxxxxxx", "--omit"],
                ["--letters", "abxxxxxx", "--omit", str(missing)],
                ["--letters", "abxxxxxx", "--omit", str(path)],
            )
            for arguments in fixtures:
                for json_mode in (True, False):
                    with self.subTest(arguments=arguments, json=json_mode):
                        with patch.object(griddle, "solve_puzzle") as solving, patch.object(griddle, "load_vocabulary") as loading:
                            code, output = self.invoke([*arguments, *(["--json"] if json_mode else [])])
                        self.assertEqual(code, 2)
                        if json_mode:
                            self.assertEqual(json.loads(output)["status"], "input_error")
                        else:
                            self.assertIn("input_error", output)
                        solving.assert_not_called()
                        loading.assert_not_called()
            self.assertEqual(path.read_text(), "mud\nm3d\n")

    def test_exclusion_loading_follows_puzzle_validation(self):
        with patch.object(griddle, "load_exclusions") as loading:
            code, _ = self.invoke(["--letters", "short", "--omit", "exclude.csv"])
        self.assertEqual(code, 2)
        loading.assert_not_called()

    def test_exclusions_with_modifiers_and_json(self):
        with tempfile.TemporaryDirectory() as directory:
            exclusions = Path(directory) / "exclude.csv"
            modifiers = Path(directory) / "modifiers.json"
            exclusions.write_text('AX," ax ",\n', encoding="utf-8")
            board = empty_modifiers()
            board[0][0] = "TW"
            modifiers.write_text(json.dumps(board))
            original_solve = griddle.solve_puzzle

            def controlled_solve(puzzle, *, omitted_words=None):
                return original_solve(puzzle, {"ab", "ax"}, omitted_words=omitted_words)

            with patch.object(griddle, "solve_puzzle", side_effect=controlled_solve) as solving:
                code, output = self.invoke([
                    "--letters", "abxxxxxx", "--modifiers", str(modifiers),
                    "--omit", str(exclusions), "--json",
                ])
            self.assertEqual(code, 0)
            result = json.loads(output)
            self.assertEqual(result["status"], "optimal")
            self.assertEqual(result["total_score"], 12)
            self.assertEqual(solving.call_args.kwargs["omitted_words"], {"ax"})
            self.assertEqual(solving.call_args.args[0].modifiers[0][0], "TW")
            self.assertEqual(set(result), {"status", "grid", "total_score", "unused_tiles", "words"})
            self.assertEqual(set(result["words"][0]), {"spelling", "direction", "coordinates", "score"})

    def test_exclusion_real_dictionary_relative_and_absolute_paths(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            inputs = Path(directory) / "inputs"
            project.mkdir()
            inputs.mkdir()
            exclusions = inputs / "exclude.csv"
            contents = b'BAX," bax "\n'
            exclusions.write_bytes(contents)
            for path in ("../inputs/exclude.csv", str(exclusions)):
                with self.subTest(path=path):
                    process = subprocess.run(
                        [sys.executable, str(root / "griddle_solver.py"), "--letters", "ABXXXXXX", "--omit", path, "--json"],
                        cwd=project, capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertEqual(process.stderr, "")
                    result = json.loads(process.stdout)
                    self.assertEqual(result["status"], "optimal")
                    self.assertTrue(all(word["spelling"] != "BAX" for word in result["words"]))
                    puzzle = griddle.parse_puzzle("ABXXXXXX")
                    restricted = griddle.load_vocabulary(puzzle) - {"bax"}
                    self.assertEqual(griddle.evaluate_board(puzzle, result["grid"], restricted)["total_score"], result["total_score"])
                    self.assertEqual(exclusions.read_bytes(), contents)

    def test_exclusion_file_is_not_implicitly_loaded(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            implicit_file = Path(directory) / "exclude.csv"
            implicit_file.write_bytes(b"invalid1\n")
            process = subprocess.run(
                [sys.executable, str(root / "griddle_solver.py"), "--letters", "ABXXXXXX", "--json"],
                cwd=directory, capture_output=True, text=True, check=False,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["total_score"], 21)
            self.assertEqual(implicit_file.read_bytes(), b"invalid1\n")
        with patch.object(griddle, "load_exclusions") as loading, \
             patch.object(griddle, "solve_puzzle", return_value={"status": "no_solution", "message": "No words"}):
            self.invoke(["--letters", "abxxxxxx"])
        loading.assert_not_called()

    def test_exclusion_real_dictionary_no_solution(self):
        root = Path(__file__).resolve().parents[1]
        puzzle = griddle.parse_puzzle("ABXXXXXX")
        with tempfile.TemporaryDirectory() as directory:
            exclusions = Path(directory) / "exclude.csv"
            with exclusions.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerows([word] for word in sorted(griddle.load_vocabulary(puzzle)))
            contents = exclusions.read_bytes()
            for json_mode in (True, False):
                process = subprocess.run(
                    [sys.executable, str(root / "griddle_solver.py"), "--letters", "ABXXXXXX", "--omit", str(exclusions), *(["--json"] if json_mode else [])],
                    cwd=directory, capture_output=True, text=True, check=False,
                )
                self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
                self.assertEqual(process.stderr, "")
                if json_mode:
                    self.assertEqual(json.loads(process.stdout)["status"], "no_solution")
                else:
                    self.assertIn("no_solution", process.stdout)
                self.assertEqual(exclusions.read_bytes(), contents)

    def test_exclusion_empty_file_preserves_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exclude.csv"
            path.write_bytes(b"")
            code, output = self.invoke(["--letters", "ABXXXXXX", "--omit", str(path), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["total_score"], 21)
            self.assertEqual(path.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()