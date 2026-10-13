# Reproducible runtime dependencies

`python.lock` declares `requirements.lock`: 16 exact SHA-256-pinned official PyPI
wheels for CPython 3.12, Linux x86_64. Public core creates a
no-system-site-packages app environment. Text/notebook serving uses stdlib; the
sealed connector lazily uses version-reviewed official host `app.auth`,
`app.config` and `app.applied_app_runtime` and the owner preparation `app.deps`
installation-owner guard. Their transitive imports are included (FastAPI,
Pydantic/settings, SQLAlchemy/greenlet, PyJWT/bcrypt). This is a scoped local
adapter, not a platform capability or a general owner bearer. Authoring verifies
the live installation-owner run before reserving a slot or saving a card. The
separately submitted sealed consumer uses the auth primitive to mint only its
short-lived own-app token. Synthetic preparation/tests use disposable settings and
no real key/login. Future owner-requested authoring validates the host’s signing
configuration through the public authentication primitive; submitted values remain
sealed.

No FFmpeg, media archives, yt-dlp, bgutil, Node/browser bundle, websockets, httpx,
cryptography client or platform site-package fallback is required by this edition.
No third-party licensing bundle was imported from fullstage. Dependency wheel
license metadata/hashes were inspected in private evidence and install succeeded
with `--no-index --require-hashes`; `pip check` passed. Wheels/venv/test
dependencies remain sibling private test evidence, not release contents. Native
dependency wheels (bcrypt/Pydantic/SQLAlchemy/greenlet) have package license
metadata; none is the unknown-license codec/browser asset that blocked fullmedia
publication.

The host must separately supply a compatible confined Codex text CLI and existing
login. Runtime text invokes no owner agent/tools/hooks/MCP/user config and strips
app/owner tokens. No provider session or generation was used for these tests.
Synthetic adapters test the boundary, not real provider availability.

Studio original app code is MIT licensed; dependency packages retain their own
licenses and notices. No bundled media or native provider archive is distributed.
