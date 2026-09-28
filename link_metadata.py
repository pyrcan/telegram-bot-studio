"""Bounded public-link metadata via the platform's SSRF-protected proxy."""
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

@dataclass
class LinkContent:
    title: str
    text: str
    metadata: dict

class PageText(HTMLParser):
    def __init__(self):
        super().__init__();self.hidden=0;self.title=False;self.head=False
        self.title_parts=[];self.parts=[];self.meta={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='meta':
            name=(attrs.get('property') or attrs.get('name') or '').lower()
            if name in ('og:title','twitter:title','og:description','twitter:description','description','author','og:site_name'):
                self.meta.setdefault(name,(attrs.get('content') or '')[:2000])
        if tag in ('script','style','noscript','svg'):self.hidden+=1
        if tag=='head':self.head=True
        if tag=='title':self.title=True
        if tag in ('p','div','br','li','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg'):self.hidden=max(0,self.hidden-1)
        if tag=='title':self.title=False
        if tag=='head':self.head=False
    def handle_data(self,data):
        if self.hidden:return
        if self.title:self.title_parts.append(data)
        elif not self.head:self.parts.append(data)
    def content(self):
        title=self.meta.get('og:title') or self.meta.get('twitter:title') or ' '.join(self.title_parts)
        metadata={'kind':'webpage','title':title.strip()[:500],
          'description':(self.meta.get('og:description') or self.meta.get('twitter:description') or self.meta.get('description') or '').strip()[:2000],
          'author':self.meta.get('author','').strip()[:300], 'site_name':self.meta.get('og:site_name','').strip()[:300]}
        text='\n'.join(s.strip() for s in ''.join(self.parts).splitlines() if s.strip())[:24000]
        return LinkContent(metadata['title'],text,metadata)

def youtube_id(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.username or parsed.password:return None
    host=(parsed.hostname or '').lower();parts=parsed.path.strip('/').split('/')
    if host=='youtu.be':candidate=parts[0]
    elif host in ('youtube.com','www.youtube.com','m.youtube.com','youtube-nocookie.com','www.youtube-nocookie.com'):
        candidate=(urllib.parse.parse_qs(parsed.query).get('v') or [''])[0] if parsed.path=='/watch' else parts[1] if len(parts)==2 and parts[0] in ('shorts','embed','live') else ''
    else:return None
    return candidate if re.fullmatch(r'[A-Za-z0-9_-]{11}',candidate) else None

def fetch_public(url):
    endpoint=os.environ['API_BASE_URL'].rstrip('/')+'/api/proxy?'+urllib.parse.urlencode({'url':url})
    request=urllib.request.Request(endpoint,headers={'Authorization':'Bearer '+os.environ['APP_TOKEN']})
    with urllib.request.urlopen(request,timeout=12) as response:
        kind=response.headers.get('Content-Type','').lower()
        # HTML metadata normally sits in the head. Keep a bounded prefix rather
        # than discarding all useful metadata because the entire page is large.
        return kind,response.read(1_000_000).decode('utf-8','replace')

def read_page(url,fetcher=fetch_public):
    video=youtube_id(url)
    if video:
        endpoint='https://www.youtube.com/oembed?'+urllib.parse.urlencode({'url':'https://www.youtube.com/watch?v='+video,'format':'json'})
        kind,raw=fetcher(endpoint);data=json.loads(raw)
        title=data.get('title')
        if not isinstance(title,str) or not title.strip():raise ValueError('No public video title')
        metadata={'kind':'video','provider':'YouTube','source':'oembed','title':title.strip()[:500],
          'author':str(data.get('author_name') or '').strip()[:300]}
        # oEmbed is metadata, never a transcript or proof of video contents.
        return LinkContent(metadata['title'],'',metadata)
    kind,raw=fetcher(url)
    if 'html' in kind:
        page=PageText();page.feed(raw);return page.content()
    if kind.startswith('text/plain'):return LinkContent('',raw[:24000],{'kind':'text'})
    raise ValueError('No readable public metadata')
