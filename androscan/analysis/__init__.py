"""Static analysis on apktool output (Smali) — call graph for now, more later.

Currently exports the call-graph public surface so callers can do::

    from androscan.analysis import call_graph
    status = call_graph.get_status(decompile_cache_dir)

See :mod:`androscan.analysis.call_graph` for the SQLite-backed index, the
parser in :mod:`androscan.analysis.smali_parser`, and the virtual-dispatch
resolver in :mod:`androscan.analysis.dispatch`.
"""
