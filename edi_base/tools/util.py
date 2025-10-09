# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Util methods for edi_base"""


def _chunks(iterable, n):
    """Split an iterable into successive n-sized chunks from iterable
    Chunk type is preserved:
        - list -> list of lists
        - recordset -> list of recordsets

    _chunks([i for i in range(7)], 0) -> []
    _chunks([i for i in range(7)], 1) -> [[0], [1], [2], [3], [4], [5], [6]]
    _chunks([i for i in range(7)], 2) -> [[0, 1], [2, 3], [4, 5], [6]]
    _chunks([i for i in range(7)], 10) -> [[0, 1, 2, 3, 4, 5, 6]]
    _chunks([], 10) -> []
    """
    if n <= 0:
        return []
    res = []
    for i in range(0, len(iterable), n):
        res.append(iterable[i : i + n])
    return res
