"""The one exception class every reader error in adichtion derives from.

A damaged, truncated or otherwise malformed file makes the reader raise a subclass of
:class:`AdichtionError` (and never an arbitrary ``IndexError``, ``struct.error`` or numpy error):
``AdichtFormatError`` (container), ``ChartDataError`` (the Map stream), ``BlocksFormatError``
(sample data), ``MacChartError``/``MacFormatError`` (LabChart for Mac documents), ``VideoStreamError``
and ``MovieError`` (video).  It is a ``ValueError``, so code that caught ``ValueError`` before keeps
working.  ``docs/FORMAT.md`` §9 lists which damage is detected where.
"""
from __future__ import annotations


class AdichtionError(ValueError):
    """Base class of every error adichtion raises for a file it cannot read."""
