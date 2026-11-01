"""Makes QR codes for short links, so a phone can join without typing.

Byte mode, low error correction, versions 1 to 5. Those versions use a single block
of data, which keeps this small and avoids another dependency.
"""

from __future__ import annotations

# Version: (data codewords, error correction codewords).
_SIZES = {1: (19, 7), 2: (34, 10), 3: (55, 15), 4: (80, 20), 5: (108, 26)}
_LOW_CORRECTION_BITS = 0b01
_FORMAT_MASK = 0b101010000010010
_FORMAT_DIVISOR = 0b10100110111
_PAD = (0xEC, 0x11)


def _field_tables() -> tuple[list[int], list[int]]:
    powers, logs = [0] * 512, [0] * 256
    value = 1
    for index in range(255):
        powers[index] = value
        logs[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    powers[255:510] = powers[:255]
    return powers, logs


_POWERS, _LOGS = _field_tables()


def _times(left: int, right: int) -> int:
    return _POWERS[_LOGS[left] + _LOGS[right]] if left and right else 0


def _correction(data: list[int], count: int) -> list[int]:
    divisor = [1]
    for power in range(count):
        grown = [0] * (len(divisor) + 1)
        for place, value in enumerate(divisor):
            grown[place] ^= value
            grown[place + 1] ^= _times(value, _POWERS[power])
        divisor = grown
    remainder = [0] * count
    for value in data:
        lead = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        for place in range(count):
            remainder[place] ^= _times(divisor[place + 1], lead)
    return remainder


def _codewords(data: bytes, version: int) -> list[int]:
    capacity, correction = _SIZES[version]
    bits = "0100" + f"{len(data):08b}" + "".join(f"{value:08b}" for value in data)
    bits += "0" * min(4, capacity * 8 - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = [int(bits[start:start + 8], 2) for start in range(0, len(bits), 8)]
    words += [_PAD[index % 2] for index in range(capacity - len(words))]
    return words + _correction(words, correction)


def _masked(mask: int, row: int, column: int) -> bool:
    return (
        (row + column) % 2 == 0,
        row % 2 == 0,
        column % 3 == 0,
        (row + column) % 3 == 0,
        (row // 2 + column // 3) % 2 == 0,
        (row * column) % 2 + (row * column) % 3 == 0,
        ((row * column) % 2 + (row * column) % 3) % 2 == 0,
        ((row + column) % 2 + (row * column) % 3) % 2 == 0,
    )[mask]


def _format_bits(mask: int) -> int:
    value = (_LOW_CORRECTION_BITS << 3) | mask
    remainder = value << 10
    for shift in range(4, -1, -1):
        if remainder & (1 << (shift + 10)):
            remainder ^= _FORMAT_DIVISOR << shift
    return ((value << 10) | remainder) ^ _FORMAT_MASK


class _Symbol:
    def __init__(self, version: int):
        self.size = 17 + 4 * version
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]
        last = self.size - 7
        for row, column in ((0, 0), (0, last), (last, 0)):
            self._finder(row, column)
        for place in range(8, self.size - 8):
            self._set(6, place, place % 2 == 0)
            self._set(place, 6, place % 2 == 0)
        if version > 1:
            self._alignment(self.size - 7)
        for place in list(range(9)) + list(range(self.size - 8, self.size)):
            self.fixed[8][place] = True
            self.fixed[place][8] = True
        self._set(self.size - 8, 8, True)

    def _set(self, row: int, column: int, dark: bool) -> None:
        self.dark[row][column] = dark
        self.fixed[row][column] = True

    def _finder(self, top: int, left: int) -> None:
        for row in range(-1, 8):
            for column in range(-1, 8):
                if 0 <= top + row < self.size and 0 <= left + column < self.size:
                    ring = max(abs(row - 3), abs(column - 3))
                    self._set(top + row, left + column, ring in (0, 1, 3))

    def _alignment(self, middle: int) -> None:
        for row in range(-2, 3):
            for column in range(-2, 3):
                self._set(middle + row, middle + column, max(abs(row), abs(column)) != 1)

    def place(self, words: list[int]) -> None:
        bits = [(word >> shift) & 1 == 1 for word in words for shift in range(7, -1, -1)]
        taken = 0
        column = self.size - 1
        upward = True
        while column > 0:
            if column == 6:
                column -= 1
            rows = range(self.size - 1, -1, -1) if upward else range(self.size)
            for row in rows:
                for side in (column, column - 1):
                    if not self.fixed[row][side]:
                        self.dark[row][side] = taken < len(bits) and bits[taken]
                        taken += 1
            upward = not upward
            column -= 2

    def masked(self, mask: int) -> list[list[bool]]:
        grid = [row[:] for row in self.dark]
        for row in range(self.size):
            for column in range(self.size):
                if not self.fixed[row][column] and _masked(mask, row, column):
                    grid[row][column] = not grid[row][column]
        bits = _format_bits(mask)
        first = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
        second = [(self.size - 1 - place, 8) for place in range(7)] + [(8, self.size - 8 + place) for place in range(8)]
        for place in range(15):
            dark = bits >> place & 1 == 1
            for row, column in (first[14 - place], second[14 - place]):
                grid[row][column] = dark
        return grid


def _penalty(grid: list[list[bool]]) -> int:
    """The standard's four scanning problems: long runs, blocks, finder look-alikes and imbalance."""
    size = len(grid)
    lines = grid + [list(column) for column in zip(*grid)]
    score = 0
    for line in lines:
        run = 1
        for place in range(1, size):
            run = run + 1 if line[place] == line[place - 1] else 1
            score += 3 if run == 5 else 1 if run > 5 else 0
        text = "".join("1" if value else "0" for value in line)
        score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for row in range(size - 1):
        for column in range(size - 1):
            if grid[row][column] == grid[row][column + 1] == grid[row + 1][column] == grid[row + 1][column + 1]:
                score += 3
    dark = sum(sum(line) for line in grid)
    return score + abs(dark * 20 // (size * size) - 10) * 10


def make(text: str) -> list[list[bool]]:
    """Rows of dark and light squares. Raises ValueError for text too long for version 5."""
    data = text.encode("utf-8")
    for version, (capacity, _) in _SIZES.items():
        if len(data) + 2 <= capacity:
            break
    else:
        raise ValueError("That text is too long for this QR code.")
    symbol = _Symbol(version)
    symbol.place(_codewords(data, version))
    return min((symbol.masked(mask) for mask in range(8)), key=_penalty)
