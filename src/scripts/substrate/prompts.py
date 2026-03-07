"""Compatibility re-exports for substrate prompt builders."""

from lib.substrate_prompt_builder import (
    write_pruner_prompt,
    write_seeder_prompt,
    write_shard_prompt,
)

__all__ = [
    "write_pruner_prompt",
    "write_seeder_prompt",
    "write_shard_prompt",
]
