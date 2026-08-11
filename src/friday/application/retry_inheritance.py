"""Exact frozen-resolution inheritance shared by every retry path."""

from __future__ import annotations

from friday.application.ports import UnitOfWork
from friday.domain.agent import RunAgentResolution
from friday.domain.identifiers import RunAgentResolutionId, RunSkillResolutionId
from friday.domain.run import Run
from friday.domain.skill import RunSkillBinding, RunSkillResolution


def inherit_frozen_resolutions_in_uow(uow: UnitOfWork, source: Run, retry: Run) -> None:
    """Copy only the exact source attempt's frozen identity and skills.

    An unresolved source remains unresolved.  This helper intentionally never
    scans siblings or reads an Agent/Skill active pointer.
    """

    source_skill_resolution = uow.run_skill_resolutions.get(source.id)
    if source_skill_resolution is not None:
        uow.run_skill_resolutions.add(
            RunSkillResolution(
                RunSkillResolutionId.new(), retry.id, source_skill_resolution.resolved_at
            )
        )
        uow.run_skill_bindings.add_all(
            [
                RunSkillBinding(
                    retry.id,
                    binding.skill_id,
                    binding.revision_id,
                    binding.position,
                )
                for binding in uow.run_skill_bindings.list_for_run(source.id)
            ]
        )

    source_agent_resolution = uow.run_agent_resolutions.get(source.id)
    if source_agent_resolution is not None:
        uow.run_agent_resolutions.add(
            RunAgentResolution(
                RunAgentResolutionId.new(),
                retry.id,
                source_agent_resolution.agent_id,
                source_agent_resolution.revision_id,
                source_agent_resolution.resolved_at,
            )
        )
