# Telegram Bot Studio

Build focused Telegram bots, try changes privately, and publish a reviewed version
for approved users. Core behaviors: blank drafts, text assistants, and Pocket Notes.

> **Review candidate, not yet a Store-ready platform release.** Requires Möbius
> support for `permissions.job_secret_read` on supervised app jobs. Older platforms
> cannot run these jobs. Submit a Store listing only after that support is released.
> No Telegram proxy, new confirmation system or service-concurrency setting is required.

## Two companion apps, separate responsibilities

| App | Role |
| --- | --- |
| **Telegram Bridge** | Your personal Möbius owner-agent channel in a paired private Telegram chat. Works on its own. |
| **Telegram Bot Studio** | Creates, configures, tests and publishes separate specialist bots. Requires Bridge's pairing to verify the Telegram owner during setup. |

**Pair Telegram Bridge first, then connect a different bot in Studio.** Each
specialist needs its own BotFather identity. Installation grants no cross-app
access, approves no users, sends no model requests and activates no bots.

Bridge can optionally manage access requests after a separate explicit approval.
Catalog navigation is a separate metadata-only connection. Neither exposes private
participant conversations or notes. Bot customization opens in Möbius; this Store
edition does not continue pre-existing owner chats from Telegram.

Bridge-generated images stay in the authenticated Möbius conversation. Bridge
settings hold the HTTPS address used for ordinary navigation links, not media keys.

The display name was formerly Assistant Studio. The `assistant-studio` package,
service and skill identities remain stable; display names are not identifiers.

## Connect → build → test → release

1. Create a blank bot, or explicitly reuse an existing bot's definition. Reuse
   copies configuration only, never connections, participant data or credentials.
2. Choose **Connect securely**. Send the prepared Möbius chat request, then enter
   the key only in its sealed card—not a chat message. The consumer verifies a
   separate bot, refuses an existing webhook and stores the key encrypted. It
   does not activate the specialist or send a test message.
3. Customize in the bot's owner editing chat and save drafts. Select a real
   runtime: instructions alone do not turn a blank bot into a working assistant.
4. Approve **Try changes** for the exact draft. Private tests route only the paired
   owner to a separate snapshot. Changed snapshots start fresh; old test data is
   retained but stopped. Other users keep the published version.
5. Review and approve publication in Möbius chat. It enables the tested definition
   for approved users and returns the owner to the live conversation. Test data
   is never promoted. Loading a draft is not proof that its behavior was tested.
6. Share the bot link. New users choose **Request access** and need owner approval
   in **Users**. Approval sends a notice and permits model usage. Links alone never
   admit users. Removal blocks future work but preserves their saved content.

Sensitive app buttons prepare editable chat drafts. Clicking does not automatically
send a model turn, grant access or publish. Existing saved approvals and sealed
input own the confirmation; app-owned helpers recheck exact revisions and epochs.
For owner operations, read [assistant-studio.md](assistant-studio.md).

## Core behaviors

- **Blank draft:** no preset purpose, no tools, no model calls.
- **Text assistant:** bounded history for that participant and published version;
  text replies only, no tools, browsing, file operations or owner-agent authority.
- **Pocket Notes:** save text/links, browse/search/filter, edit themes and review
  deterministic digests. Explicit questions or AI summaries use the model.
  Website metadata is fetched through Möbius's existing protected proxy;
  YouTube metadata is not a transcript or evidence of watching a video.

Notes have recoverable Trash. Permanent deletion requires a separate exact-snapshot
confirmation; it also clears that participant's saved AI answer history and cached
previews. Other notes/users stay. No automatic note expiry runs.

Optional Notebook mode is off by default. After explicit participant consent, new
captures may leave Telegram only after the note and its Saved reply are confirmed.
Questions and answers stay. Separate recent-chat cleanup reviews tracked messages
under 48 hours old; it never guesses IDs or deletes notes. Telegram deletion cannot
be undone; uncertain sends/deletes are not automatically repeated.

## Privacy and limits

- Management exposes bot configuration and user identity/access metadata, **not**
  participant content, sessions or notebook counts. Old participant-management
  routes remain denied, even for valid legacy sessions.
- Each verified private Telegram identity owns its own records. The hosting
  operator can technically access server data; this is not end-to-end encryption.
- AI replies send relevant content to the connected model provider. Deleting local
  data does not erase Telegram/provider copies or pre-existing backups.
- AI replies require the platform's connected **Codex** account and a model
  available to it. Usage and charges follow that account. Choose the model before
  live testing. Capture/navigation need no model. Limits: 50 requests per
  participant per rolling day, two pending tasks, eight Telegram connections.
- The reviewed job reads only `tg-1` through `tg-8` from its own encrypted secret
  store. Keys stay out of browser responses/logs and are not cached between calls.
  A compromised authorized job could leak those keys; revocation cannot retract
  a key already read. App code is trusted, not an arbitrary-code OS sandbox.
- The participant text runner has no owner/app bearers, tools, MCP, hooks or
  repository context. It uses the installed Codex login through the CLI, without
  copying credentials into prompts or participant state.
- Soft-deleted bots stop serving and retain data/connection slots. Restore stays
  paused. Moving a retained connection needs a separate reviewed owner operation;
  it verifies stored keys in RAM and never copies participant content.
- Pausing/revoking/deleting cannot recall external work already in flight.

## Architecture and verification

Telegram polling, outbox claims, menus, lifecycle, private tests and participant
isolation live in this app. Existing Möbius supervised jobs provide single-flight
execution; the encrypted secret store and saved chat cards remain platform-owned.
Transactions fence duplicate sends and stale reviews. Definite rate limits retry;
ambiguous delivery is surfaced instead of silently duplicated.

Run `python3 -m pytest -q` from this directory. Tests use temporary stores and fake
Telegram/model responses. The runner boundary test drives the installed CLI against
a localhost fake server with synthetic credentials, not the real provider.
For synthetic UI tests see [TESTING.md](TESTING.md). No tests should point at a live
installed app or use real participant data. A Telegram phone check remains separate.

This source-only core package contains no credentials, runtime databases, private
conversations, media converters or native archives. Do not replace a customized
installation with additional runtime keys using this core package.

## License

MIT; see [LICENSE](LICENSE). Runtime UI dependencies are supplied by Möbius, not
vendored here. Telegram and Codex are third-party services; this project is not an
official Telegram product. Store screenshots use synthetic data.
