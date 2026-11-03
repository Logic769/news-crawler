"""Text helpers shared by the extractors."""

from __future__ import annotations

import unicodedata


def get_longest_common_sub_string(str1: str, str2: str) -> str:
    """Return the longest common substring of two strings.

    Uses a rolling array so the auxiliary space is O(min(n, m)).
    """
    if not all([str1, str2]):
        return ""
    if len(str1) < len(str2):
        str1, str2 = str2, str1
    previous_row = [0] * (len(str2) + 1)
    max_length = 0
    start_position = 0
    for index_of_str1 in range(1, len(str1) + 1):
        current_row = [0] * (len(str2) + 1)
        for index_of_str2 in range(1, len(str2) + 1):
            if str1[index_of_str1 - 1] == str2[index_of_str2 - 1]:
                current_row[index_of_str2] = previous_row[index_of_str2 - 1] + 1
                if current_row[index_of_str2] > max_length:
                    max_length = current_row[index_of_str2]
                    start_position = index_of_str1 - max_length
        previous_row = current_row
    return str1[start_position : start_position + max_length]


def normalize_text(html: str) -> str:
    """Normalize HTML source text with NFKC, matching the legacy behavior."""
    return unicodedata.normalize("NFKC", html)
