# Telegram Bot Studio

For building, UX and troubleshooting across Studio and Telegram Bridge, read
the shared `telegram-bot-building` skill when installed. It owns the concise
workflow and proven lessons; `assistant-studio.md` owns operational detail.

Current setup requires Telegram Bridge installed and paired to the owner.
Studio verifies that pairing when connecting a specialist bot; it does not yet
have standalone owner pairing. Bot serving and participant data remain separate.

Telegram Bot Studio manages the owner's focused assistants. For requests to list,
create, configure, publish locally, or check these assistants/bots, read the
installed `assistant-studio.md` skill and query live Studio state. This applies
in Möbius and the owner's personal Telegram conversation; do not rely on old
chat memory or confuse the owner agent with a restricted specialist. Current
channel availability must be checked, not inferred from an assistant's name.
Existing approvals and private participant boundaries remain unchanged.

Customize bot opens a dedicated ordinary owner editing chat. Inspect the exact
bot ID first, save drafts, and wait for explicit publication. New assistants default to
blank, unconfigured drafts with no capabilities or model calls, unless the owner
explicitly chooses an existing bot as a starting point. Reuse never copies user
data or connections; notes-v1 is one behavior, not Studio's universal runtime.
New actions require reviewed bot-specific implementation, not just instructions.
Never give specialist participants the owner agent or load their private content
into editing chats. Read assistant-studio.md for the versioned extension path.

In a bot customization conversation, run `owner.py context` from Studio's
installed source_dir before choosing a bot; it resolves this CHAT_ID's durable
binding without private user data. This also works when the composer suggestion
was cleared or navigation interrupted. Do not infer a bot from its name alone.

When customizing a blank bot, choose its draft runtime explicitly: conversation-v1
for text assistance, or a reviewed specialist implementation. Instructions alone
do not switch blank-v1 into a functioning assistant.

The developer flow is connect-first: connect Telegram securely, explicitly start
private testing, customize/save drafts, Try changes in Telegram, then explicitly
Publish changes for approved users. Tests have separate fresh-per-changed-snapshot
data and owner-only routing; live participants keep the published version.
Saving or applying app source never authorizes activation, paid live tests or
publication. Studio buttons prepare editable owner-chat requests. Reuse saved approval and sealed-input cards; read the skill before executing an app-owned helper.

New Telegram users request access and need explicit owner approval in Studio → Users.
That list exposes identity/access metadata only, never conversations or notes.
Old invitation links do not bypass approval. Access changes use trusted per-person
confirmation; preserve stored content and never remove a real user merely to test.
