"""Calculate the next semantic version based on git tags and release type.

Usage:
    INPUT_RELEASE_TYPE=patch python .github/tools/calculate_version.py

Outputs:
    tag_name: The next version tag (e.g., v0.2.0)
"""

import os
import re
import subprocess
import sys


def get_latest_tag() -> str | None:
    """Fetch the latest git tag sorted by creation date."""
    try:
        output = subprocess.check_output(
            ["git", "tag", "--sort=-creatordate"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for tag in output.splitlines():
            if re.match(r"^v?\d+\.\d+\.\d+$", tag):
                return tag
    except subprocess.CalledProcessError as e:
        print(f"Error fetching git tags: {e}", file=sys.stderr)
    return None


def bump_version(version: str, part: str) -> str:
    """Bump the major, minor, or patch part of a semver string."""
    major, minor, patch = map(int, version.lstrip("v").split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor}.{patch}"


def main() -> None:
    release_type = os.environ.get("INPUT_RELEASE_TYPE", "").strip().lower()
    github_output = os.environ.get("GITHUB_OUTPUT")

    if release_type not in ["major", "minor", "patch"]:
        sys.exit(
            f"::error::Invalid release type '{release_type}'. Must be one of: major, minor, patch."
        )

    print(f"Auto-incrementing: {release_type}")
    latest = get_latest_tag()
    if not latest:
        sys.exit(
            "::error::No existing tags found to increment from! "
            "Please create an initial tag (e.g. v0.1.0) manually."
        )

    tag_name = f"v{bump_version(latest, release_type)}"
    print(f"Resolved: {latest} -> {tag_name}")

    if github_output:
        with open(github_output, "a") as f:
            f.write(f"tag_name={tag_name}\n")
    else:
        print(f"OUTPUT: tag_name={tag_name}")


if __name__ == "__main__":
    main()
