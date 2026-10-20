# All values in this file vary b/t game instances; update accordingly per game
LETTER_VALUES = {
    letter: value
    for letters, value in (
        ("aeiostr", 1), ("dnul", 2), ("ygh", 3), ("fbcmpw", 4),
        ("kv", 5), ("x", 8), ("jqz", 10),
    )
    for letter in letters
}

BOARD_MODIFIERS = [
  [None, None, None, None],
  [None, None, None, None],
  [None, None, None, None],
  ["DW", None, None, None]
]