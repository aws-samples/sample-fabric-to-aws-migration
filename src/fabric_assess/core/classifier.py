"""Entity population classification.

Tables (and external tables) are scored on both axes. Views, materialized views,
and routines are REBUILT — they have no data to move, so Migration Effort is 0
and only Query Complexity applies.
"""
from __future__ import annotations

from fabric_assess.models import EntityPopulation, EntityType

_REBUILT_TYPES = frozenset(
    {EntityType.VIEW, EntityType.MATERIALIZED_VIEW, EntityType.ROUTINE}
)


def classify_population(entity_type: EntityType) -> EntityPopulation:
    """Map an entity type to its scoring population."""
    if entity_type in _REBUILT_TYPES:
        return EntityPopulation.REBUILT
    return EntityPopulation.TABLE
