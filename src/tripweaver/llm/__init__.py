"""Request parsing boundary.

Phase one uses a deterministic parser. A future LLM implementation must return the
same canonical ``TripRequest`` model and cannot bypass domain validation.
"""

from tripweaver.llm.constraint_parser import DeterministicConstraintParser

__all__ = ["DeterministicConstraintParser"]
