"""Staleness system: change detection, content hashing, freshness tracking.

Public API (import from submodules):
    alignment_change_tracker: alignment_changed_pending, check_pending,
        invalidate_excerpts, set_flag
    change_detection: diff_files, snapshot_files
    freshness_service: compute_section_freshness
    hash_service: content_hash, file_hash
"""
