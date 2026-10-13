"""Owner workspace appearance, separate from draft and published behavior."""
import json
from assistant_store import Problem, uid

def read(root, identity, definition):
    fallback = {'icon': ''.join(word[0] for word in definition['name'].split()[:2]).upper() or 'B',
                'purpose': definition.get('description') or 'Describe what this bot is for',
                'revision': '0'}
    return root.config('display:' + identity, fallback)

def save(catalog, identity, body):
    if set(body) - {'icon','purpose','expected_revision'}:
        raise Problem('Appearance supports only icon and purpose.')
    for field, limit in (('icon',32),('purpose',160)):
        value = body.get(field)
        if not isinstance(value,str) or not value.strip() or len(value)>limit:
            raise Problem(f'Enter a {field} within {limit} characters.')
    with catalog.open(identity) as selected:
        definition = selected.config('draft')
    with catalog.root.db:
        catalog.root.db.execute('BEGIN IMMEDIATE')
        current = read(catalog.root,identity,definition)
        if current['revision'] != body.get('expected_revision'):
            raise Problem('Appearance changed elsewhere. Refresh before saving.',409)
        value = {'icon':body['icon'].strip(),'purpose':body['purpose'].strip(),'revision':uid()}
        catalog.root.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)',
                                ('display:'+identity,json.dumps(value)))
    return value
