# Public-core feature matrix

Reference: public `9ebca6c8584400e6adc419ee8196a0fb82d19896`.
This is a separate source edition, never an in-place fullmedia downgrade.

| Operation | Edition support and authority |
|---|---|
| Blank draft / config-only reuse | Real existing UI + owner helper; no implicit Pocket Notes purpose, credentials, users or content copied |
| Text conversation | conversation-v1, same-participant/release last-eight context; confined no-tools Codex runner; actual provider compatibility/availability not exercised |
| Personal notebook | notes-v1 capture, links, participant-only search/themes/digest/timezone/Trash/confirmed cleanup; title/creator oEmbed is metadata only |
| Draft/name/instructions/model / cosmetic icon+purpose | Existing UI/save guards; published version unchanged |
| Connect separate bot | Actual owner-chat saved sealed card; reviewed local accepted-runtime + backend consuming saved-command adapter, own app-scoped 5-minute token for exact public encrypted secret PUT; no owner bearer/app password/activation |
| Pair owner directly | Existing UI code + identity-only worker polling + verified private Telegram sender; Bridge not needed |
| Create/resume canonical customization chat | Actual owner.py customize + current owner agent token, unchanged guarded service + public owner chat API; app link navigation is not approval |
| Start/load/stop private test | Actual owner-chat saved exact approval; unchanged revision/epoch/generation/test fences, fresh separate changed-snapshot data |
| Release exact tested draft | Actual saved exact owner approval; immutable release, no test-data promotion/no new user grants |
| Users read/change | Metadata UI preserved; allow/block only actual saved per-person approval, old state rejected, owner protected |
| Pause / delete / restore / reconnect | Safe pause retained; delete/restore/reconnect separately approved owner helper, metadata fences/recoverable data, reconnect read-only transient own-key verification |
| Dashboard/cost assumptions | Content-free aggregate diagnostics and estimates, no private paid-provider pilot controls or diagnostic ledger |
| Optional Bridge | Config-only navigation after independent approval. Current compatible Bridge omits user-management controls/grant writer; Studio retains its fail-closed legacy narrow owned-bots-v1 interoperability routes but no grant is created or implied. No owner-chat continuation grant |

## Explicit omissions, not ignored descriptors

- `app.owner-action` capability and all `service.owner_actions`: public core
  does not provide them. No forged APP_OWNER_ACTION, widened actor guard or
  owner-token fallback replaces them. UI routes human requests to actual chats.
- `credentialed_fetch` and binary-upload providers; local Telegram large-file
  descriptors: absent. Bounded fixed-method job-owned Telegram HTTP replaces
  text/metadata transport, not upload. Frames never read keys.
- File response / stream descriptors and public audio/video/verification endpoints:
  absent; service is private and public callers fail closed.
- Every YouTube MP3/MP4/selectable-media/browser runtime, private OpenAI pilot,
  paid-serving settings/permits, codecs/FFmpeg/yt-dlp/bgutil bundles, remote/local
  browser controls and their UI/skills/dependencies/assets: not shipped.
- Exact Bridge customization-owner-chat grants: unavailable in public core and
  not fabricated. Canonical customization stays in actual owner Möbius chat.

The registry accepts only notes-v1, blank-v1 and conversation-v1. Old unsupported
keys raise a domain error; no hidden old-runtime redefinition/fallback. Package
contains no bot state, keys, grants, users, histories, private tests or releases.
Original Studio app code is MIT licensed; see LICENSE. Dependencies retain
their own notices and licenses. Real connection/pairing/model/Telegram/phone execution remains
unverified because only source preparation and synthetic testing were authorized.
