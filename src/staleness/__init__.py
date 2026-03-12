"""Staleness system: change detection, content hashing, freshness tracking.

Public API (import from submodules):
    change_tracker: alignment_changed_pending, check_pending,
        invalidate_excerpts, set_flag
    file_differ: diff_files, snapshot_files
    freshness_calculator: compute_section_freshness
    content_hasher: content_hash, file_hash
"""
