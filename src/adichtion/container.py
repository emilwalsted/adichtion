"""Container layer of ADInstruments LabChart ``.adicht`` files.

An ``.adicht`` file is an "ADInstruments Compound File": a structured-storage
container (in the spirit of Microsoft OLE/CFBF, but ADI's own format) with
fixed-size clusters, a doubly linked cluster map and a tree of storages that
hold named streams.  See ``docs/FORMAT.md`` for the byte-level description.

Everything in this module is pure Python (``struct`` + ``mmap``); it does not
depend on numpy and never decodes sample data.
"""
from __future__ import annotations

import mmap
import os
import struct
from dataclasses import dataclass, field
from typing import Iterator, Optional

MAGIC = "ADInstruments Compound File".encode("utf-16le")
HEADER_SIZE = 512
ENTRY_SIZE = 160
NAME_BYTES = 72

TYPE_STORAGE = 1
TYPE_STREAM = 2


class AdichtFormatError(ValueError):
    """Raised when a file does not look like an ADInstruments compound file."""


@dataclass
class Entry:
    """One directory entry (a stream or a sub-storage)."""

    name: str
    first: int          # first cluster (see AdichtContainer.offset for the mapping)
    size: int           # size in bytes
    last: int           # last cluster
    flags: int
    type: int           # TYPE_STORAGE or TYPE_STREAM (0 has been seen for empty streams)
    path: str = ""      # "/"-joined path from the root, filled in by tree()
    children: Optional[list["Entry"]] = None

    @property
    def is_storage(self) -> bool:
        return self.children is not None

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Entry":
        name = raw[:NAME_BYTES].decode("utf-16le", "replace").split("\x00", 1)[0]
        first, size, last = struct.unpack_from("<qqq", raw, 0x48)
        flags, typ = struct.unpack_from("<ii", raw, 0x88)
        return cls(name=name, first=first, size=size, last=last, flags=flags, type=typ)


class AdichtContainer:
    """Read-only access to the streams of an ``.adicht`` file.

    The file is memory mapped; streams are materialised as ``bytes`` on demand
    with :meth:`read_stream`.  Use it as a context manager or call
    :meth:`close`.
    """

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._fh = open(self.path, "rb")
        try:
            self.data: mmap.mmap | bytes = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):  # empty file or platform quirk
            self.data = self._fh.read()
        d = self.data
        if len(d) < HEADER_SIZE or d[: len(MAGIC)] != MAGIC:
            self.close()
            raise AdichtFormatError(f"{self.path!r} is not an ADInstruments Compound File")
        (self.version, self.header_size, self.chunk_size, _, _, _, log2cl) = struct.unpack_from("<7i", d, 0x80)
        if not (8 <= log2cl <= 20):
            self.close()
            raise AdichtFormatError(f"implausible cluster size 2**{log2cl}")
        self.cluster_size = 1 << log2cl
        self.capacity, self.neg_capacity, self.map_first = struct.unpack_from("<qqq", d, 0x9C)
        self.root = Entry.from_bytes(d[0xB8 : 0xB8 + ENTRY_SIZE])
        self.n_clusters = (len(d) - self.header_size) // self.cluster_size
        self.entries_per_page = self.cluster_size // 8
        self._tree: Optional[Entry] = None

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        try:
            if isinstance(self.data, mmap.mmap):
                self.data.close()
        finally:
            self._fh.close()

    def __enter__(self) -> "AdichtContainer":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- clusters ------------------------------------------------------------
    def offset(self, cluster: int) -> int:
        """File offset of a cluster: the first data cluster after the header is
        cluster 2 (clusters 0 and 1 are virtual)."""
        return self.header_size + (cluster - 2) * self.cluster_size

    def cluster(self, cluster: int) -> bytes:
        o = self.offset(cluster)
        return bytes(self.data[o : o + self.cluster_size])

    def map_entry(self, cluster: int) -> tuple[int, int]:
        """(next, prev) links of a cluster from the cluster map; 0 = none."""
        page, idx = divmod(cluster, self.entries_per_page)
        o = self.offset(self.map_first + page) + idx * 8
        return struct.unpack_from("<ii", self.data, o)

    def chain(self, first: int, last: int) -> Iterator[int]:
        """Yield the clusters of a stream by following the cluster map."""
        c = first
        seen = 0
        limit = self.n_clusters + 2
        while True:
            yield c
            seen += 1
            if c == last or seen > limit:
                return
            nxt, _prev = self.map_entry(c)
            if nxt <= 0:
                return
            c = nxt

    def read_stream(self, entry: Entry) -> bytes:
        """Return the content of a stream entry (``b""`` for empty entries)."""
        if entry.first <= 0 or entry.size <= 0:
            return b""
        clusters = list(self.chain(entry.first, entry.last))
        # fast path: contiguous run
        if clusters[-1] - clusters[0] == len(clusters) - 1:
            o = self.offset(clusters[0])
            return bytes(self.data[o : o + entry.size])
        buf = bytearray()
        for c in clusters:
            buf += self.cluster(c)
            if len(buf) >= entry.size:
                break
        return bytes(buf[: entry.size])

    # -- directory -----------------------------------------------------------
    @staticmethod
    def parse_table(blob: bytes) -> Optional[list[Entry]]:
        """Parse a directory table: int32 count, int32 -count, then 160-byte entries."""
        if len(blob) < 8:
            return None
        n, neg = struct.unpack_from("<ii", blob, 0)
        if not (1 <= n <= 4096 and neg == -n and len(blob) >= 8 + n * ENTRY_SIZE):
            return None
        entries = []
        for i in range(n):
            e = Entry.from_bytes(blob[8 + i * ENTRY_SIZE : 8 + (i + 1) * ENTRY_SIZE])
            if not e.name or any(ord(ch) < 32 for ch in e.name):
                return None
            entries.append(e)
        return entries

    def _load_table(self, e: Entry) -> Optional[list[Entry]]:
        if e.first <= 0:
            return None
        blob = self.cluster(e.first)  # tables have always fitted in one cluster
        return self.parse_table(blob[: e.size] if e.size else blob)

    def tree(self) -> Entry:
        """Return the root storage with ``children`` filled in recursively."""
        if self._tree is None:
            root = self.root
            root.path = ""
            root.children = self._load_table(root) or []
            self._descend(root.children, "")
            self._tree = root
        return self._tree

    def _descend(self, entries: list[Entry], prefix: str) -> None:
        for e in entries:
            e.path = f"{prefix}{e.name}"
            looks_like_table = e.first > 0 and e.size >= 8 + ENTRY_SIZE and (e.size - 8) % ENTRY_SIZE == 0
            if looks_like_table or e.type == TYPE_STORAGE:
                sub = self._load_table(e)
                if sub:
                    e.children = sub
                    self._descend(sub, e.path + "/")

    def walk(self) -> Iterator[Entry]:
        """Depth-first iteration over all entries (storages and streams)."""

        def rec(entries: list[Entry]) -> Iterator[Entry]:
            for e in entries:
                yield e
                if e.children:
                    yield from rec(e.children)

        yield from rec(self.tree().children or [])

    def streams(self) -> dict[str, Entry]:
        """Mapping path -> entry for all streams (non-storage entries)."""
        return {e.path: e for e in self.walk() if not e.is_storage}

    def find_stream(self, name: str) -> Optional[Entry]:
        """Find a stream by case-insensitive leaf name (e.g. ``"Blocks_0"``)."""
        name = name.lower()
        for e in self.walk():
            if not e.is_storage and e.name.lower() == name:
                return e
        return None
