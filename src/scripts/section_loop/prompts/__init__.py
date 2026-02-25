"""Prompt generation package for the section loop.

Re-exports all public names from the writers module so existing
``from section_loop.prompts import ...`` imports continue to work.
"""

from .writers import (
    agent_mail_instructions,
    signal_instructions,
    write_impl_alignment_prompt,
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
    write_section_setup_prompt,
    write_strategic_impl_prompt,
)

__all__ = [
    "agent_mail_instructions",
    "signal_instructions",
    "write_impl_alignment_prompt",
    "write_integration_alignment_prompt",
    "write_integration_proposal_prompt",
    "write_section_setup_prompt",
    "write_strategic_impl_prompt",
]
