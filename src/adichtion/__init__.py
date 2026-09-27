"""adichtion – read ADInstruments LabChart ``.adicht`` files without LabChart.

Public API::

    from adichtion import AdichtFile

    with AdichtFile(path) as f:
        f.records            # list[Record]
        f.channels           # list[ChannelInfo]
        f.comments           # list[Comment]
        rec = f.records[0]
        rec.read(channel, start_tick, stop_tick)   # numpy float64, converted to units when possible
"""
from .container import AdichtContainer, AdichtFormatError
from .file import AdichtFile, Record, Selection
from .model import BlockInfo, ChannelInfo, ChannelSetup, ChartData, Comment, RecordInfo, UnitsConversion

__version__ = "0.1.0"

__all__ = [
    "AdichtFile", "Record", "Selection", "AdichtContainer", "AdichtFormatError",
    "BlockInfo", "ChannelInfo", "ChannelSetup", "ChartData", "Comment", "RecordInfo", "UnitsConversion",
    "__version__",
]
