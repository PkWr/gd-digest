#!/usr/bin/env python3
"""
build-embed.py — generates embed.html from template.html + gd-digest.md.

At build time:
  1. Parses gd-digest.md to find all entry URLs (first link per entry)
  2. Fetches og:image from each URL concurrently
  3. Skips generic/logo images using heuristics
  4. Injects Image: lines into a markdown copy (doesn't modify gd-digest.md)
  5. Injects content at BUILD_INJECT marker in template.html
  6. Writes self-contained embed.html with INLINE_MD baked in

Usage: python3 build-embed.py
"""

import json, os, re, sys
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlparse, urljoin
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, as_completed

# Always resolve paths relative to this script's location
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Content (vault) is the master; this script only reads it.
CONTENT = os.environ.get('GD_DIGEST_CONTENT') or os.path.expanduser(
    '~/Vaults/2026-PkWr/70 Sandpit/Sandpit - GD Digest/Claude - GD Digest')
SRC_HTML      = 'template.html'
SRC_MD        = os.path.join(CONTENT, 'gd-digest.md')
SRC_TOOLS     = os.path.join(CONTENT, 'tools.md')
SRC_GLOSSARY  = os.path.join(CONTENT, 'glossary.md')
SRC_TUTORIALS = os.path.join(CONTENT, 'tutorials.md')
OUT_HTML      = '../embed.html'
TIMEOUT  = 8   # seconds per request
WORKERS  = 12  # concurrent fetches

# Patterns that suggest a generic/logo og:image rather than event artwork
SKIP_PATTERNS = [
    r'/logo[s\-_]',
    r'[\-_]logo\.',
    r'/icon[s\-_]',
    r'[\-_]icon\.',
    r'/favicon',
    r'placeholder',
    r'default[\-_]image',
    r'og[\-_]default',
    r'social[\-_]default',
    r'fallback',
]
SKIP_RE = re.compile('|'.join(SKIP_PATTERNS), re.IGNORECASE)


class OGParser(HTMLParser):
    """Minimal parser — extracts og:image and twitter:image from <head>."""
    def __init__(self):
        super().__init__()
        self.image = None
        self._in_head = True

    def handle_starttag(self, tag, attrs):
        if not self._in_head:
            return
        if tag == 'body':
            self._in_head = False
            return
        if tag != 'meta':
            return
        d = dict(attrs)
        prop = d.get('property', '') or d.get('name', '')
        if prop in ('og:image', 'og:image:url') and not self.image:
            self.image = d.get('content', '').strip()
        if prop == 'twitter:image' and not self.image:
            self.image = d.get('content', '').strip()


def fetch_og_image(url):
    """Return (url, og_image_url | None). Fetches only enough HTML to find og:image."""
    try:
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; GDDigestBot/1.0)',
            'Accept': 'text/html',
        })
        with urlopen(req, timeout=TIMEOUT) as resp:
            # Read up to 32 KB — og:image is always in <head>
            raw = resp.read(32768).decode('utf-8', errors='replace')
        parser = OGParser()
        parser.feed(raw)
        img = parser.image
        if not img:
            return url, None
        # Resolve relative URLs
        if img.startswith('//'):
            img = 'https:' + img
        elif img.startswith('/'):
            p = urlparse(url)
            img = f'{p.scheme}://{p.netloc}{img}'
        # Skip generic images
        if SKIP_RE.search(img):
            return url, None
        return url, img
    except Exception:
        return url, None


def parse_entries(md):
    """
    Minimal markdown parser — returns list of dicts:
      { title, first_url, has_image }
    where first_url is the first [text](url) in the entry,
    and has_image is True if an *Image:* line is already present.
    """
    entries = []
    current = None
    SEP = ' · '

    for raw in md.split('\n'):
        line = raw.rstrip()

        if line == '---':
            current = None
            continue

        if line.startswith('### ') and SEP in line:
            parts = line[4:].split(SEP)
            if len(parts) >= 3:
                current = {
                    'title':     SEP.join(parts[2:]).strip(),
                    'first_url': None,
                    'has_image': False,
                }
                entries.append(current)
            continue

        if current is None:
            continue

        if re.match(r'^\*Image:\s*https?://', line):
            current['has_image'] = True
            continue

        if current['first_url'] is None and re.search(r'\[([^\]]+)\]\(([^)]+)\)', line):
            m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', line)
            if m:
                current['first_url'] = m.group(2)

    return entries


def inject_images(md, image_map):
    """
    For each entry in image_map { title: image_url },
    inject an *Image: url* line after the *Type:* metadata line
    (only if one isn't already present).
    """
    lines  = md.split('\n')
    out    = []
    current_title = None
    injected      = set()
    SEP = ' · '

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # Detect entry heading
        if stripped.startswith('### ') and SEP in stripped:
            parts = stripped[4:].split(SEP)
            if len(parts) >= 3:
                current_title = SEP.join(parts[2:]).strip()

        out.append(line)

        # After *Type:* metadata line, inject *Image:* if we have one
        if (current_title
                and current_title not in injected
                and re.match(r'^\*Type:', stripped)):
            img_url = image_map.get(current_title)
            if img_url:
                out.append(f'*Image: {img_url}*')
                injected.add(current_title)

        i += 1

    return '\n'.join(out)


def build_embed(html, md_augmented, glossary_md, tutorials_md):
    """Inject inlined content at the BUILD_INJECT marker in template.html."""
    md_json        = json.dumps(md_augmented)
    glossary_json  = json.dumps(glossary_md)
    tutorials_json = json.dumps(tutorials_md)
    embed = html

    # Replace BUILD_INJECT placeholder with inlined content
    try:
        start = embed.index('\n// ── BUILD_INJECT ──')
        end   = embed.index('</script>', start)
        inline = '\nconst INLINE_MD = __MD__;\nconst INLINE_GLOSSARY = __GLOSSARY__;\nconst INLINE_TUTORIALS = __TUTORIALS__;\ninitCalendar(INLINE_MD, INLINE_GLOSSARY, INLINE_TUTORIALS);\n'
        embed = embed[:start] + inline + embed[end:]
    except ValueError:
        pass  # marker not found — template may already be built

    embed = embed.replace('__MD__', md_json)
    embed = embed.replace('__GLOSSARY__', glossary_json)
    embed = embed.replace('__TUTORIALS__', tutorials_json)
    return embed


def main():
    with open(SRC_HTML) as f:
        html = f.read()
    with open(SRC_MD) as f:
        md = f.read()
    try:
        with open(SRC_TOOLS) as f:
            tools_md = f.read()
        print(f'Loaded tools ({len(tools_md):,} chars)')
    except FileNotFoundError:
        tools_md = ''
        print('tools.md not found — skipping')
    try:
        with open(SRC_GLOSSARY) as f:
            glossary_md = f.read()
        print(f'Loaded glossary ({len(glossary_md):,} chars)')
    except FileNotFoundError:
        glossary_md = ''
        print('glossary.md not found — skipping')
    try:
        with open(SRC_TUTORIALS) as f:
            tutorials_md = f.read()
        print(f'Loaded tutorials ({len(tutorials_md):,} chars)')
    except FileNotFoundError:
        tutorials_md = ''
        print('tutorials.md not found — skipping')

    # Combine calendar + tools for the main parser (tools.md has ## Tools section)
    md_combined = md + '\n' + tools_md

    # 1. Parse entries to find URLs
    entries = parse_entries(md_combined)
    to_fetch = [(e['title'], e['first_url'])
                for e in entries
                if e['first_url'] and not e['has_image']]

    print(f'Found {len(entries)} entries, fetching og:images for {len(to_fetch)}…')

    # 2. Fetch og:images concurrently
    image_map = {}
    if to_fetch:
        url_to_title = {url: title for title, url in to_fetch}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch_og_image, url): url for _, url in to_fetch}
            for fut in as_completed(futures):
                url, img = fut.result()
                title = url_to_title[url]
                if img:
                    image_map[title] = img
                    print(f'  ✓  {title[:50]}')
                else:
                    print(f'  –  {title[:50]}')

    print(f'\nFetched images for {len(image_map)} / {len(to_fetch)} entries')

    # 3. Inject Image: lines into a copy of the combined markdown
    md_augmented = inject_images(md_combined, image_map)

    # 4. Build embed.html
    embed = build_embed(html, md_augmented, glossary_md, tutorials_md)

    with open(OUT_HTML, 'w') as f:
        f.write(embed)

    print(f'\n✓ {OUT_HTML} written ({len(embed):,} chars)')


if __name__ == '__main__':
    main()
