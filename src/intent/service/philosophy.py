"""Global philosophy bootstrap helpers.

This module is a thin re-export facade.  All implementation has been
split into:

- ``philosophy_classifier``  — signal classifiers
- ``philosophy_catalog``     — file-system catalog + principle extraction
- ``philosophy_dispatch``    — agent dispatch with retry
- ``philosophy_bootstrap``   — orchestration, constants, grounding validation

Existing callers (and monkey-patching in ``loop_bootstrap``) continue
to work because every public and private name is re-exported here.
"""

from __future__ import annotations

# ── re-export: classifier ─────────────────────────────────────────────
from intent.service.philosophy_classifier import (  # noqa: F401
    MIN_USER_SOURCE_BYTES as MIN_USER_SOURCE_BYTES,
    VALID_SOURCE_TYPES as VALID_SOURCE_TYPES,
    _classify_distiller_result as _classify_distiller_result,
    _classify_guidance_result as _classify_guidance_result,
    _classify_list_signal_result as _classify_list_signal_result,
    _classify_selector_result as _classify_selector_result,
    _classify_verifier_result as _classify_verifier_result,
    _guidance_schema_error as _guidance_schema_error,
    _invalid_source_map_detail as _invalid_source_map_detail,
    _malformed_signal_result as _malformed_signal_result,
    _manifest_source_mode as _manifest_source_mode,
    _preserve_malformed_signal as _preserve_malformed_signal,
    _user_source_is_substantive as _user_source_is_substantive,
)

# ── re-export: catalog ────────────────────────────────────────────────
from intent.service.philosophy_catalog import (  # noqa: F401
    _declared_principle_ids as _declared_principle_ids,
    build_philosophy_catalog as build_philosophy_catalog,
    walk_md_bounded as walk_md_bounded,
)

# ── re-export: dispatch ───────────────────────────────────────────────
from intent.service.philosophy_dispatch import (  # noqa: F401
    _attempt_output_path as _attempt_output_path,
    _dispatch_classified_signal_stage as _dispatch_classified_signal_stage,
    _dispatch_with_signal_check as _dispatch_with_signal_check,
    _record_stage_attempt as _record_stage_attempt,
)

# ── re-export: bootstrap (constants, helpers, orchestration) ──────────
from intent.service.philosophy_bootstrap import (  # noqa: F401
    BOOTSTRAP_DECISIONS_NAME as BOOTSTRAP_DECISIONS_NAME,
    BOOTSTRAP_GUIDANCE_NAME as BOOTSTRAP_GUIDANCE_NAME,
    BOOTSTRAP_SIGNAL_NAME as BOOTSTRAP_SIGNAL_NAME,
    BOOTSTRAP_STATUS_NAME as BOOTSTRAP_STATUS_NAME,
    USER_SOURCE_NAME as USER_SOURCE_NAME,
    _block_bootstrap as _block_bootstrap,
    _bootstrap_decisions_path as _bootstrap_decisions_path,
    _bootstrap_diagnostics_path as _bootstrap_diagnostics_path,
    _bootstrap_guidance_path as _bootstrap_guidance_path,
    _bootstrap_result as _bootstrap_result,
    _bootstrap_signal_path as _bootstrap_signal_path,
    _bootstrap_status_path as _bootstrap_status_path,
    _clear_bootstrap_signal as _clear_bootstrap_signal,
    _collect_bootstrap_context_artifacts as _collect_bootstrap_context_artifacts,
    _grounding_failure_source_mode as _grounding_failure_source_mode,
    _request_user_philosophy as _request_user_philosophy,
    _run_bootstrap_prompter as _run_bootstrap_prompter,
    _timestamp_now as _timestamp_now,
    _user_source_path as _user_source_path,
    _write_bootstrap_decisions as _write_bootstrap_decisions,
    _write_bootstrap_diagnostics as _write_bootstrap_diagnostics,
    _write_bootstrap_signal as _write_bootstrap_signal,
    _write_bootstrap_status as _write_bootstrap_status,
    _write_user_source_template as _write_user_source_template,
    ensure_global_philosophy as ensure_global_philosophy,
    sha256_file as sha256_file,
    validate_philosophy_grounding as validate_philosophy_grounding,
)

# ── module-level attributes used by loop_bootstrap monkey-patching ────
# loop_bootstrap.py assigns these attributes at module level:
#   _philosophy_bootstrap.dispatch_agent = ...
#   _philosophy_bootstrap.read_agent_signal = ...
#   _philosophy_bootstrap.read_model_policy = ...
#   _philosophy_bootstrap.write_validated_prompt = ...
#
# The actual dispatch_agent / write_validated_prompt used at runtime live
# in philosophy_bootstrap.py and philosophy_dispatch.py respectively.
# We keep the imports here so the monkey-patch assignments land on this
# module object (which is what loop_bootstrap imports).
from dispatch.engine.section_dispatch import dispatch_agent as dispatch_agent  # noqa: F401
from dispatch.service.model_policy import (  # noqa: F401
    load_model_policy as read_model_policy,
)
from dispatch.service.prompt_guard import (  # noqa: F401
    write_validated_prompt as write_validated_prompt,
)
