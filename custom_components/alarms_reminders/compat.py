"""Validation library import that works across Home Assistant versions.

Home Assistant 2026.10 replaced voluptuous with probatio, which has the same
Schema/Required/Optional API. Older versions only have voluptuous.
"""

try:
    import probatio as vol
except ImportError:  # Home Assistant before 2026.10
    import voluptuous as vol

__all__ = ["vol"]
