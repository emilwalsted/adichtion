"""adichtion – read ADInstruments LabChart ``.adicht`` files without LabChart.

Public API::

    from adichtion import AdichtFile

    with AdichtFile(path) as f:
        f.records            # list[Record]
        f.channels           # list[ChannelInfo]
        f.comments           # list[Comment]
        rec = f.records[0]
        rec.read(channel, start_tick, stop_tick)   # numpy float64 in the channel's units (or volts)
        rec.block_envelope(channel)                # per-block min/max from the file's own index
        rec.video                                  # the video clip recorded with the record, if any

    from adichtion.infer import infer_record       # recover calculated-channel definitions from the data
    from adichtion.macchart import channel_settings   # ... or read them from a LabChart for Mac document
"""
from .container import AdichtContainer, AdichtFormatError
from .file import AdichtFile, Record, Selection
from .model import (BlockInfo, ChannelBlock, ChannelInfo, ChannelRecordInfo, ChannelSetup, ChartData, Comment,
                    RecordInfo, UnitsConversion, VideoClip)

__version__ = "0.3.0"

__all__ = [
    "AdichtFile", "Record", "Selection", "AdichtContainer", "AdichtFormatError",
    "BlockInfo", "ChannelBlock", "ChannelInfo", "ChannelRecordInfo", "ChannelSetup", "ChartData", "Comment",
    "RecordInfo", "UnitsConversion", "VideoClip",
    "__version__",
]
