"""Public product copy and participant-scoped, deterministic onboarding.

No new account, model invocation, or parallel memory system. Admission and
notebook identity remain owned by telegram_store.member.
"""
DESCRIPTION = ('Your personal notebook in Telegram.\n\n'
               'Save thoughts and links. Find them later, ask about your notes, or review a digest.\n\n'
               'Private pilot: tap Start, then Request access. The owner must approve you. No extra account needed.\n\n'
               'Notebooks are separate between users. The hosting operator can access stored data; AI answers share relevant content with the model provider.')
SHORT_DESCRIPTION = 'Save notes and links. Find them later. Your own notebook in Telegram. Owner approval required.'
PRIVACY = ('Your notebook is separate from other users. The hosting operator can access stored data; '
           'AI answers share note content with the model provider. Details: /privacy')


def has_saved_before(store, participant):
    return bool(store.db.execute('SELECT 1 FROM notes WHERE participant=? LIMIT 1',(participant,)).fetchone()
                or store.db.execute('SELECT 1 FROM note_tombstones WHERE participant=? LIMIT 1',(participant,)).fetchone())


def welcome(name, returning=False):
    opening = ('Welcome back. Your notebook is ready.\n\nSend a note or paste a link to save it.' if returning
               else 'Your notebook is ready.\n\nSend your first note or paste a link. No folders or setup needed.')
    return f'{name}\n{opening}\n\nFind your saved notes with /notes. The menu also has Ask, Digest and Settings.\n\n{PRIVACY}'


def help_text(name):
    return (f'{name}\nSave a thought. Find it when it matters.\n\n'
            'Send text or a link to save it.\n/notes — browse and search\n/ask — ask about your notes\n'
            '/digest — review what you saved\n/settings — preferences and cleanup\n\n'+PRIVACY)


def saved_text(store, participant, note):
    from note_views import preferences, saved_at
    text='Saved · '+saved_at(note,preferences(store,participant)['timezone'],True)
    # Content-free tombstones prevent replay/deletion from restarting first-save
    # coaching. No per-person onboarding flags or note-content copy is needed.
    other=store.db.execute('SELECT 1 FROM notes WHERE participant=? AND id!=? LIMIT 1',(participant,note['id'])).fetchone()
    deleted=store.db.execute('SELECT 1 FROM note_tombstones WHERE participant=? LIMIT 1',(participant,)).fetchone()
    if not other and not deleted:text+='\n\nYour first note is saved. Find it anytime with /notes, or send another.'
    return text
