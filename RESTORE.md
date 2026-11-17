# Restore this project

This backup preserves the project source, tests, documentation, and application assets as of 2026-11-17.

1. Clone this repository or download its source archive.
2. Install the Python version specified in the project README and create a virtual environment.
3. Install dependencies: `python -m pip install -r "requirements.txt"`. Consult the README for optional build dependencies.

Before launching, run `python restore_assets.py` to download the large runtime asset from this repository’s GitHub release and verify its SHA-256 checksum. It is not included in the source ZIP.

Credentials, saved login state, local run outputs, logs, virtual environments, and built executables are excluded. Re-enter credentials and install external tools on the new device. Rebuild executables from source if needed; no executable was rebuilt for this backup. Company services still require their usual network and account access.
