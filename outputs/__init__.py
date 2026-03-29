"""Output plugin registry for autofeeder.

Auto-detects which outputs are configured for a profile and dispatches
digest data to each.  Individual output failures are logged but never
crash the pipeline.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger("autofeeder")

# Type alias for an output callable.
OutputFn = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]


def _resolve_env(value: str) -> str:
    """Resolve a ``$VAR`` reference to its environment value.

    Returns the raw string unchanged if it does not start with ``$``.
    Returns empty string if the env var is not set.
    """
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def _is_set(value: str | None) -> bool:
    """Return True if *value* is a non-empty string that isn't an unresolved
    ``$VAR`` placeholder."""
    if not value:
        return False
    resolved = _resolve_env(value)
    return bool(resolved)


def get_outputs(profile: dict[str, Any], config: dict[str, Any]) -> list[OutputFn]:
    """Return a list of output callables enabled for *profile*.

    Detection rules:
    - **markdown** — always enabled.
    - **slack** — enabled if ``profile["outputs"]["slack"]["webhook"]`` is
      configured (non-empty after env resolution, not ``$UNSET_VAR``).
    - **obsidian** — enabled if ``profile["outputs"]["obsidian"]["vault_path"]``
      is set.
    - **email** — enabled if ``profile["outputs"]["email"]["recipients"]`` is a
      non-empty list.
    """
    outputs: list[OutputFn] = []
    profile_outputs: dict[str, Any] = profile.get("outputs", {})

    # Markdown — always on
    from outputs.markdown import publish as md_publish  # noqa: E402
    outputs.append(md_publish)
    logger.debug("Output enabled: markdown")

    # Slack
    slack_cfg = profile_outputs.get("slack", {})
    webhook_raw = slack_cfg.get("webhook", "")
    if _is_set(webhook_raw):
        from outputs.slack import publish as slack_publish  # noqa: E402
        outputs.append(slack_publish)
        logger.debug("Output enabled: slack")

    # Obsidian
    obsidian_cfg = profile_outputs.get("obsidian", {})
    vault_path = obsidian_cfg.get("vault_path", "")
    if vault_path:
        from outputs.obsidian import publish as obsidian_publish  # noqa: E402
        outputs.append(obsidian_publish)
        logger.debug("Output enabled: obsidian")

    # Email
    email_cfg = profile_outputs.get("email", {})
    recipients = email_cfg.get("recipients", [])
    if isinstance(recipients, list) and len(recipients) > 0:
        from outputs.email import publish as email_publish  # noqa: E402
        outputs.append(email_publish)
        logger.debug("Output enabled: email")

    return outputs


def publish_all(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Run every configured output plugin.

    Each plugin runs independently — if one fails the rest still execute.
    Failures are logged as errors but never re-raised.
    """
    outputs = get_outputs(profile, config)
    logger.info("Publishing digest via %d output(s)", len(outputs))

    for output_fn in outputs:
        module_name = getattr(output_fn, "__module__", "unknown")
        try:
            output_fn(digest_data, profile, config)
            logger.info("Output succeeded: %s", module_name)
        except Exception:
            logger.exception("Output FAILED: %s (continuing)", module_name)
