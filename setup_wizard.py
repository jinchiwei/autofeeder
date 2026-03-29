"""Interactive setup wizard for autofeeder.

Guides new users through API key configuration, validates the connection,
and optionally creates a first profile via --discover.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _prompt(question: str, default: str = "") -> str:
    """Prompt user with optional default."""
    if default:
        raw = input(f"{question} [{default}]: ").strip()
        return raw if raw else default
    return input(f"{question}: ").strip()


def _choose(question: str, options: list[str], default: int = 0) -> int:
    """Present numbered options, return index of choice."""
    print(f"\n{question}")
    for i, opt in enumerate(options):
        marker = " (default)" if i == default else ""
        print(f"  {i + 1}) {opt}{marker}")
    while True:
        raw = input(f"\nChoice [1-{len(options)}]: ").strip()
        if not raw:
            return default
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        print(f"  Please enter 1-{len(options)}")


def _validate_anthropic_direct(api_key: str) -> bool:
    """Test a direct Anthropic API key."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=30)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        return bool(resp.content)
    except Exception as e:
        print(f"  Connection failed: {e}")
        return False


def _validate_openai(api_key: str, base_url: str | None = None) -> bool:
    """Test an OpenAI-compatible API key."""
    try:
        from openai import OpenAI
        kwargs = {"api_key": api_key, "timeout": 30}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        return bool(resp.choices)
    except Exception as e:
        print(f"  Connection failed: {e}")
        return False


def run_setup() -> None:
    """Run the interactive setup wizard."""
    print()
    print("=" * 50)
    print("  autofeeder setup")
    print("=" * 50)
    print()
    print("This wizard will configure your API key and")
    print("create a .env file so autofeeder can run.")
    print()

    # Check if .env already exists
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text()
        has_keys = any(
            line.split("=", 1)[1].strip()
            for line in content.splitlines()
            if "=" in line and not line.strip().startswith("#")
            and line.split("=", 1)[1].strip()
        )
        if has_keys:
            print(".env file already exists with configured keys.")
            overwrite = _prompt("Overwrite? (y/n)", "n")
            if overwrite.lower() != "y":
                print("Setup cancelled. Your .env is unchanged.")
                return

    # Choose backend
    backend_idx = _choose(
        "Which LLM provider do you want to use?",
        [
            "Anthropic (direct API) -- recommended, get a key at console.anthropic.com",
            "Anthropic via AWS Bedrock -- for institutional/enterprise deployments",
            "OpenAI -- gpt-4o and newer",
            "OpenAI-compatible endpoint -- custom base URL (Versa, Azure, etc.)",
        ],
        default=0,
    )

    env_lines = []

    if backend_idx == 0:
        # Direct Anthropic
        print("\nPaste your Anthropic API key (starts with sk-ant-):")
        api_key = _prompt("ANTHROPIC_API_KEY")
        if not api_key:
            print("No key provided. Exiting.")
            return

        print("  Testing connection...")
        if _validate_anthropic_direct(api_key):
            print("  Connected successfully!")
        else:
            print("  Could not connect. Saving anyway -- you can fix the key in .env later.")

        env_lines = [
            "# Anthropic direct API",
            f"ANTHROPIC_API_KEY={api_key}",
        ]

    elif backend_idx == 1:
        # Bedrock
        print("\nAWS Bedrock credentials:")
        aws_key = _prompt("AWS_ACCESS_KEY_ID")
        aws_secret = _prompt("AWS_SECRET_ACCESS_KEY")
        aws_region = _prompt("AWS_REGION", "us-west-2")
        base_url = _prompt("ANTHROPIC_BEDROCK_BASE_URL (leave empty for default AWS)", "")

        env_lines = [
            "# Anthropic via AWS Bedrock",
            f"AWS_ACCESS_KEY_ID={aws_key}",
            f"AWS_SECRET_ACCESS_KEY={aws_secret}",
            f"AWS_REGION={aws_region}",
        ]
        if base_url:
            env_lines.append(f"ANTHROPIC_BEDROCK_BASE_URL={base_url}")

        print("  Bedrock credentials saved. Test by running: autofeeder --profile example")

    elif backend_idx == 2:
        # OpenAI
        print("\nPaste your OpenAI API key (starts with sk-):")
        api_key = _prompt("OPENAI_API_KEY")
        if not api_key:
            print("No key provided. Exiting.")
            return

        print("  Testing connection...")
        if _validate_openai(api_key):
            print("  Connected successfully!")
        else:
            print("  Could not connect. Saving anyway -- fix the key in .env later.")

        env_lines = [
            "# OpenAI",
            f"OPENAI_API_KEY={api_key}",
            "AUTOFEEDER_BACKEND=openai",
        ]

    elif backend_idx == 3:
        # Custom endpoint
        print("\nOpenAI-compatible endpoint:")
        base_url = _prompt("Base URL (e.g., https://your-endpoint.com/v1)")
        api_key = _prompt("API key (or press enter if not needed)", "not-needed")

        print("  Testing connection...")
        if _validate_openai(api_key, base_url):
            print("  Connected successfully!")
        else:
            print("  Could not connect. Saving anyway -- fix in .env later.")

        env_lines = [
            "# OpenAI-compatible endpoint",
            f"OPENAI_API_KEY={api_key}",
            "AUTOFEEDER_BACKEND=openai",
        ]

    # Optional: Unpaywall email
    print()
    print("Optional: Unpaywall email (for academic paper full text access).")
    print("No signup needed -- just an email for API identification.")
    unpaywall = _prompt("Email (or press enter to skip)", "")
    if unpaywall:
        env_lines.append(f"\n# Unpaywall")
        env_lines.append(f"UNPAYWALL_EMAIL={unpaywall}")

    # Write .env
    env_content = "\n".join(env_lines) + "\n"
    env_path.write_text(env_content)
    print(f"\n  .env written successfully.")

    # Update config.toml model IDs for the chosen backend
    if backend_idx == 0:
        # Direct API -- use standard model names
        _update_config_models(
            model="claude-opus-4-6",
            triage_model="claude-sonnet-4-6",
        )
    elif backend_idx == 2:
        # OpenAI
        _update_config_models(
            model="gpt-4o",
            triage_model="gpt-4o",
            section="openai",
        )

    # Offer to create first profile
    print()
    print("-" * 50)
    create = _prompt("Create your first profile? (y/n)", "y")
    if create.lower() == "y":
        topic = _prompt("What topic do you want to track?")
        if topic:
            print(f"\n  Discovering feeds for: {topic}")
            print("  This takes 20-30 seconds...\n")
            try:
                from config import load_config
                from discover import discover_feeds_sync, save_discovered_profile
                import re

                config = load_config("config.toml")
                result = discover_feeds_sync(topic, config)

                profile_name = re.sub(r"[^\w\s-]", "", topic.lower())
                profile_name = re.sub(r"[\s]+", "-", profile_name).strip("-")[:40]

                path = save_discovered_profile(topic, result, profile_name)
                print(f"  Profile saved to: {path}")
                print(f"\n  Run it with: autofeeder --profile {profile_name}")
            except Exception as e:
                print(f"  Discovery failed: {e}")
                print("  You can try later with: autofeeder --discover \"your topic\"")

    print()
    print("=" * 50)
    print("  Setup complete!")
    print("=" * 50)
    print()
    print("Next steps:")
    print("  autofeeder --discover \"topic\"    # create more profiles")
    print("  autofeeder --profile <name>      # run a profile")
    print("  autofeeder --all                 # run all profiles")
    print()


def _update_config_models(
    model: str,
    triage_model: str,
    section: str = "anthropic",
) -> None:
    """Update config.toml with the right model IDs for the chosen backend."""
    config_path = Path("config.toml")
    if not config_path.exists():
        return

    content = config_path.read_text()

    # Simple replacement -- find the model lines in the right section
    lines = content.splitlines()
    in_section = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == f"[{section}]"

        if in_section:
            if stripped.startswith("model =") and not stripped.startswith("model = #"):
                # Skip comment lines
                if not stripped.startswith("#"):
                    line = f'model = "{model}"'
            elif stripped.startswith("triage_model =") and not stripped.startswith("#"):
                line = f'triage_model = "{triage_model}"'

        new_lines.append(line)

    config_path.write_text("\n".join(new_lines))
