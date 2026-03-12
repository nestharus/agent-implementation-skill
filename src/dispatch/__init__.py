"""Dispatch system: agent execution, prompt rendering, model policy.

Public API (import from submodules):
    model_policy: load_model_policy, resolve
    prompt_guard: validate_dynamic_content, write_validated_prompt
    prompt_template: TASK_SUBMISSION_SEMANTICS, render_template
    section_dispatcher: check_agent_signals, dispatch_agent, read_model_policy,
        summarize_output, write_model_choice_signal
"""
