"""Decision-model adapters. The authority layer never imports a specific model."""

from .base import DecisionAdapter, advice_from

__all__ = ["DecisionAdapter", "advice_from"]
