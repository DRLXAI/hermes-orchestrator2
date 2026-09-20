"""Effect observers: independent evidence of what an agent ACTUALLY did.

An observer's evidence must not originate from the agent being evaluated. That is the whole
point, and it is the one property no library can check for you -- so each observer documents
precisely what it reads and what it assumes.
"""

from .git import GitEffectObserver, GitObservation, assess_git_effect

__all__ = ["GitEffectObserver", "GitObservation", "assess_git_effect"]
