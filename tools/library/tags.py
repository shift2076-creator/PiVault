"""Normalise Calibre-Web tags to PiVault's closed vocabulary. Runs on pivault as snx.

Every book ends up with tags from three fixed lists only:
  * COLLECTIONS - which PiVault list the book came from (set by the books job).
  * TRADES      - the trade a manual teaches (set by the books job).
  * SUBJECTS    - the 19 Standard Ebooks subjects (standardebooks.org/manual, section 9).

Other tags (Library of Congress headings, often split at commas by Calibre-Web uploads,
PDF keywords, video ids, URLs) are mapped onto SUBJECTS or TRADES by RULES, then dropped.
EPUB files are also read for se:subject (Standard Ebooks) and dc:subject (LCSH) so books
the job added with only a collection tag gain a subject.

Books no rule can place are tagged from manual-tags.tsv (book id, exact title, tags),
reviewed by hand; a book whose title no longer matches its id is skipped with a warning.
Books still without a subject lose their non-vocabulary tags and are listed for review.

Default is a dry run that changes nothing. --apply writes a backup of every book's
current tags first; --restore BACKUP puts those tags back.
"""
import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

LIBRARY = '/media/snx/C29C87F99C87E66F/Server/Books'
BOOKS_LOCK = '/home/snx/pi-services/books/state/run.lock'
MANUAL = Path(__file__).with_name('manual-tags.tsv')

COLLECTIONS = ('PiVault Americana', 'PiVault Founding Documents', 'PiVault Trades')
TRADES = (
    'Blacksmithing', 'Bookkeeping', 'Butchery', 'Carpentry', 'Dairy', 'Dentistry',
    'Draft Animals', 'Electrical', 'Electronics and Radio', 'Engines', 'Farming',
    'Food Preservation', 'Forestry', 'Herbal Pharmacy', 'Hunting and Fishing',
    'Leather and Shoemaking', 'Machining and Metalwork', 'Masonry', 'Medicine',
    'Midwifery', 'Milling and Baking', 'Paper and Bookbinding', 'Plumbing',
    'Pottery and Glass', 'Poultry and Bees', 'Ranching', 'Soap and Chemistry',
    'Surveying and Navigation', 'Textiles', 'Veterinary', 'Water and Sanitation', 'Welding',
)
# Standard Ebooks subjects; "Children's" uses a plain apostrophe so it is easy to type.
SUBJECTS = (
    'Adventure', 'Autobiography', 'Biography', "Children's", 'Comedy', 'Drama', 'Fantasy',
    'Fiction', 'Horror', 'Memoir', 'Mystery', 'Nonfiction', 'Philosophy', 'Poetry', 'Satire',
    'Science Fiction', 'Shorts', 'Spirituality', 'Travel',
)
VOCABULARY = {t.casefold(): t for t in COLLECTIONS + TRADES + SUBJECTS}
VOCABULARY['children’s'] = "Children's"
GENRES = {'Adventure', 'Fantasy', 'Horror', 'Mystery', 'Science Fiction'}
LITERARY = {'Drama', 'Fiction', 'Poetry', 'Shorts'}  # any of these rules out Nonfiction

JUNK = re.compile(
    r'^(none|unknown|https?://.*|[0-9xX -]{10,17}|[0-9 .?-]+|'
    r'(january|february|march|april|may|june|july|august|september|october|november|december) \d{4})$',
    re.I)
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')  # e.g. a YouTube id in a PDF's keywords
WORD = re.compile(r'[A-Z]?[a-z-]+')

# (pattern, tags). Every matching rule contributes; patterns are searched case-insensitively.
RULES = [
    (r'science fiction|interplanetary|space (flight|ships|colonies|travel)|life on other planets'
     r'|time travel|robots -|human-alien|dystopia|utopia|end of the world|comets|asteroids'
     r'|lost continents|earth \(planet\)', ('Science Fiction',)),
    (r'detective|mystery|private investigators|murder.*investigation|holmes, sherlock', ('Mystery',)),
    (r'horror|gothic|ghost|vampire|werewol|haunted|paranormal|parapsycholog|monsters.*fiction'
     r'|frankenstein', ('Horror',)),
    (r'fantasy|fairy tales|imaginary place|magic.*juvenile|dragons|oz \(', ('Fantasy',)),
    (r'adventure|sea stories|pirates|buried treasure|castaways|shipwreck|western stories'
     r'|spy stories|war stories|intelligence service', ('Adventure',)),
    (r'juvenile|children\W?s (stories|fiction)', ("Children's",)),
    (r'short stories|frame-stories', ('Shorts',)),
    (r'satire|mock-heroic', ('Satire',)),
    (r'humor|humorous|wit and|limericks|nonsense verses', ('Comedy',)),
    (r'drama|tragedies|comedies|comedy plays', ('Drama',)),
    (r'poetry|poems|epic literature|lays\b|limericks|nonsense verses|novels in verse', ('Poetry',)),
    (r'autobiograph|personal narratives|slaves\W? writings', ('Autobiography',)),
    (r'memoir', ('Memoir',)),
    (r'biography', ('Biography',)),
    (r'description and travel|voyages and travels|voyages around the world$|^walking$', ('Travel',)),
    (r'philosophy|ethics|taoism|pragmatism|utilitarianism|political science|social contract'
     r'|^liberty|^knowledge$|civil disobedience|toleration', ('Philosophy',)),
    (r'religio|devotional|christian|buddhis|bible|^god\b|spiritual|catholic|^conversion$'
     r'|experience \(religion\)', ('Spirituality',)),
    (r'fiction|romances?\b|bildungsroman|stories$|allegor|fables|parables|picaresque'
     r'|myth|legend|folklore|nibelungen|trojan war', ('Fiction',)),
    (r'history|essays|economics|constitutional|commercial policy|military art|government'
     r'|disarmament|imperialism$|slavery|abolitionists|social problems$|codes and standards'
     r'|hydrogen|permitting|tea ceremony|cooking|cookbook|translations into english|wars?$'
     r'|social life and customs|homes and haunts|wilderness areas|native americans|african americans$'
     r'|^(nature|wildlife|animals|solitude|psychology|food supply|food|tea|growth factors|indexes)$',
     ('Nonfiction',)),
    (r'kiln|lumber|wood (drying|moisture|shrinkage|defects|warping|cracking|structure)'
     r'|drying (defects|rate)|softwood|hardwood|forestry|^air dry$|sample boards|solar (kiln|lumber'
     r'|heated|collector|energy)|sustainable wood', ('Forestry',)),
    (r'woodworking', ('Carpentry',)),
    (r'fishing|angling|learn to fish|i fish ny', ('Hunting and Fishing',)),
    (r'^tanning', ('Leather and Shoemaking',)),
    (r'crochet', ('Textiles',)),
    (r'^agriculture$|^apples$|^4-h$', ('Farming',)),
]
RULES = [(re.compile(p, re.I), t) for p, t in RULES]
TRADE_ONLY = re.compile(r'fiction|drama|poetry', re.I)  # a trade keyword inside a story is not a manual


def map_tag(tag):
    """Return the vocabulary tags one raw tag stands for (empty for junk or unknown)."""
    tag = tag.strip()
    if tag.casefold() in VOCABULARY:
        return {VOCABULARY[tag.casefold()]}
    if not tag or JUNK.match(tag) or (VIDEO_ID.fullmatch(tag) and not WORD.fullmatch(tag)):
        return set()
    found = set()
    for pattern, tags in RULES:
        if pattern.search(tag):
            found.update(t for t in tags if t in SUBJECTS or not TRADE_ONLY.search(tag))
    return found


def normalise(tags):
    """Return the sorted vocabulary tags for one book's raw tags."""
    out = set()
    for tag in tags:
        out |= map_tag(tag)
    if out & GENRES:
        out.add('Fiction')
    if out & LITERARY:
        out.discard('Nonfiction')
    return sorted(out, key=str.casefold)


def epub_subjects(path):
    """se:subject and dc:subject values from an EPUB's package file; empty on any error."""
    try:
        with zipfile.ZipFile(path) as book:
            container = ElementTree.fromstring(book.read('META-INF/container.xml'))
            rootfile = container.find('.//{*}rootfile').get('full-path')
            package = ElementTree.fromstring(book.read(rootfile))
    except (OSError, KeyError, AttributeError, zipfile.BadZipFile, ElementTree.ParseError):
        return []
    found = [m.text for m in package.iterfind('.//{*}meta') if m.get('property') == 'se:subject' and m.text]
    found += [s.text for s in package.iter('{http://purl.org/dc/elements/1.1/}subject') if s.text]
    return [s.strip() for s in found if s.strip()]


def calibredb(library, *args):
    result = subprocess.run(['calibredb', *args, '--library-path', library],
                            capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise SystemExit(f'calibredb {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout


def books(library):
    raw = calibredb(library, 'list', '--fields', 'title,tags,formats', '--for-machine')
    return json.loads(raw)


def set_tags(library, book_id, tags):
    calibredb(library, 'set_metadata', '--field', 'tags:' + ','.join(tags), str(book_id))


def load_manual(path=None):
    """Return {book id: (title, tags)} from the hand-reviewed TSV; every tag must be in the vocabulary."""
    manual, path = {}, path or MANUAL
    if not Path(path).is_file():
        return manual
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        book_id, title, tag_list = line.split('\t')
        chosen = [t.strip() for t in tag_list.split(',')]
        unknown = [t for t in chosen if t.casefold() not in VOCABULARY]
        if unknown:
            raise SystemExit(f'{path}:{number}: not in the vocabulary: {", ".join(unknown)}')
        manual[int(book_id)] = (title, chosen)
    return manual


def plan(library_books, manual=None):
    """Return (changes, untagged) where changes maps id -> (title, old, new)."""
    manual = load_manual() if manual is None else manual
    changes, untagged = {}, []
    for book in library_books:
        title = book.get('title', '')
        old = sorted(book.get('tags') or [], key=str.casefold)
        sources = list(old)
        for path in book.get('formats') or []:
            if str(path).lower().endswith('.epub'):
                sources += epub_subjects(path)
        if book['id'] in manual:
            if manual[book['id']][0] == title:
                sources += manual[book['id']][1]
            else:
                print(f'WARNING: manual-tags.tsv book {book["id"]} is "{manual[book["id"]][0]}" '
                      f'but the library has "{title}"; skipped.', file=sys.stderr)
        new = normalise(sources)
        if not (set(new) - set(COLLECTIONS)):
            untagged.append((book['id'], title))
        if new != old:
            changes[book['id']] = (title, old, new)
    return changes, untagged


def report(changes, untagged, library_books, out=sys.stdout):
    before = Counter(t for b in library_books for t in (b.get('tags') or []))
    after = Counter()
    changed = set(changes)
    for book in library_books:
        after.update(changes[book['id']][2] if book['id'] in changed else (book.get('tags') or []))
    print(f'Books: {len(library_books)}; to change: {len(changes)}; '
          f'left without a vocabulary tag: {len(untagged)}', file=out)
    print(f'Distinct tags: {len(before)} now, {len(after)} after', file=out)
    print('\nTags after normalising:', file=out)
    for tag, count in sorted(after.items(), key=lambda kv: kv[0].casefold()):
        print(f'  {count:5}  {tag}', file=out)
    if untagged:
        print('\nNo subject could be derived (add these to manual-tags.tsv):', file=out)
        for book_id, title in untagged:
            print(f'  {book_id:6}  {title}', file=out)


def lock():
    """Hold the books job's lock so a nightly round and this tool never overlap."""
    path = Path(BOOKS_LOCK)
    if not path.parent.is_dir():
        return None
    handle = open(path, 'a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('A books round is running; try again when it finishes.')
    return handle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--library', default=LIBRARY)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true', help='write the new tags (after a backup)')
    group.add_argument('--restore', metavar='BACKUP', help='put back the tags saved in BACKUP')
    args = parser.parse_args(argv)
    held = lock()  # noqa: F841 - kept open until exit
    if args.restore:
        saved = json.loads(Path(args.restore).read_text())
        for book_id, tags in saved.items():
            set_tags(args.library, book_id, tags)
        print(f'Restored tags on {len(saved)} books from {args.restore}')
        return 0
    library_books = books(args.library)
    changes, untagged = plan(library_books)
    report(changes, untagged, library_books)
    if not args.apply:
        print('\nDry run: nothing changed. Re-run with --apply to write these tags.')
        return 0
    backups = Path(args.library).parent / '.pivault-tag-backups'
    backups.mkdir(exist_ok=True)
    backup = backups / time.strftime('tags-%Y%m%d-%H%M%S.json')
    backup.write_text(json.dumps({str(i): changes[i][1] for i in changes}, indent=1))
    print(f'\nBackup of current tags: {backup}')
    for done, (book_id, (_, _, new)) in enumerate(changes.items(), 1):
        set_tags(args.library, book_id, new)
        if done % 50 == 0 or done == len(changes):
            print(f'  {done}/{len(changes)} books updated', flush=True)
    print(f'Done. To undo: python3 {Path(__file__).name} --restore {backup}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
