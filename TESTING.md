# Synthetic verification

Use a clean public Möbius checkout at the version documented in
COMPATIBILITY.md and its backend development Python environment. No live app,
Telegram account, provider session, key, user or bot data is needed.

```sh
# Point to the public checkout; tests import its real auth/card primitives.
export MOBIUS_CORE_SOURCE=/path/to/mobius/backend
python3 -m pytest -q
```

The test configuration replaces all runtime settings with a temporary SQLite
fixture, removes owner credentials, and denies unmocked network and subprocess
execution. Tests cover participant isolation, release/test revision fences,
access approval, lifecycle recovery, notebooks, uncertain delivery, bounded
transport and sealed setup authoring/consuming provenance. Production dependency
versions and wheel hashes are in requirements.lock. Test-only pytest is provided
by the platform development environment; no test environment is distributed.

The UI was separately exercised with the actual JSX and synthetic service
responses at 320, 390 and 1000 pixels in light and dark themes. Screenshots contain
fictional examples. These checks do not establish real Telegram delivery,
provider availability or phone behavior.
