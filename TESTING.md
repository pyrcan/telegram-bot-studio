# Isolated verification

Run `python3 -m pytest -q` from the repository root with Python 3.12 and pytest.
All data is synthetic and temporary. Do not pass real bot keys or participant data.
Studio's runner test additionally needs the supported platform Codex CLI; it
uses a fake localhost response server and temporary login/config files, not the
live provider or installed owner credentials.

## Browser fixtures

Use a Möbius development environment for the existing UI compiler/dependencies.
`MOBIUS_PLATFORM_SOURCE` defaults to `/data/platform`. Run `python3 preview.py`.
It serves only a temporary, synthetic page at `http://127.0.0.1:8768/studio.html`.
Every app fetch is replaced by a fixture response; unknown requests fail.
Never substitute an installed app URL or authenticated owner preview.

Open that exact URL with `agent-browser`, set the viewport, and run:

```sh
node test_publish_flow.cjs
node test_users_flow.cjs

```

The tests require `AGENT_BROWSER_PROFILE` to reference that browser's profile,
Node with built-in fetch/WebSocket, and the explicit synthetic-page marker.
They intercept editable chat handoffs; no live grant, bot publication, model
request or Telegram send occurs. Reload the fixture between suites. Stop the
preview with Ctrl-C and close the test browser when finished.

The fixture's app IDs, users and bot names are arbitrary synthetic values, not
installation identifiers. For Bridge, `?fresh=1` shows unpaired first-time setup.
Real Telegram client rendering and real connection behavior require a separately
approved end-to-end test; these fixtures do not prove those conditions.
