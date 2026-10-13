# Telegram Bot Studio — public-core compatible text edition

Community text edition, verified against public Möbius core `9ebca6c8584400e6adc419ee8196a0fb82d19896`.
No platform change is required. Review COMPATIBILITY.md before upgrading another edition; media runtimes and their data are not migrated.

See `COMPATIBILITY.md` for the exact supported/omitted matrix and
`assistant-studio.md` for the owner procedure. Blank/text/notebook definitions,
participant isolation, direct Telegram owner pairing, draft/private-test/release
fences, approval-only users, cosmetic metadata and canonical owner customization
chats are retained. Current management UI remains for real configuration/read
operations; sensitive actions visibly request an actual owner-chat saved approval.
There is no app-owned password input or fake capability fallback.

App-owned bounded Telegram HTTPS uses transient named own-job secret reads.
Saved sealed setup uses an accepted-runtime/nonce/epoch/slot-pinned local adapter
and exact backend consuming saved-card spec/chat check, with the public core auth primitive to mint a short-lived own-app token solely
for one encrypted secret PUT. It never mints/reads owner authority or relaxes
Studio's owner-only service guards. Only separately approved submission runs it.
Canonical owner chat creation uses an actual approved owner agent, not an app.

A clean app-owned 16-wheel hashed lock replaces the fullmedia 26-wheel lock.
No wheels, virtual environment, state, third-party media bundles or native media
assets ship here. See `DEPENDENCIES.md`. Repository-only synthetic tests are documented in TESTING.md. They are not installed app inputs. A compatible host
Codex confined text CLI remains required; no provider CLI was launched by tests.

Studio's original app code is MIT licensed; see LICENSE. Python dependencies
retain their own licenses and notices. This release makes no live transport,
phone interaction, provider availability or real-user verification claim.
