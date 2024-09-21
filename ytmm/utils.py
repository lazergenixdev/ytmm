import re

re_feat    = re.compile(r'\(feat\. .*\)')
re_invalid = re.compile(r'[^ 0-9A-Za-z_]')
re_space   = re.compile(r'\s+')
re_bracket = re.compile(r'\[.*\]')
re_garbage = re.compile(r'\(Official .*\)|\(From .*\)|\(feat\. .*\)')


def file_name_from_title(title: str):
    # Remove (feat. {})
    title = re_garbage.sub('', title).strip()
    
    # Remove invalid filename characters
    title = re_invalid.sub('', title).strip()
    
    # Remove extra spaces
    title = re_space.sub(' ', title)
    
    # Final step, lowercase and adding '_'
    return title.lower().replace(' ', '_')


def parse_title(old: str):
    # Remove garbage from title
    old = re_garbage.sub('', old).strip()

    if '\u2013' in old: # whyyyyy???? 😑
        artists, _, title = old.partition(' \u2013 ')
    else:
        artists, _, title = old.partition(' - ')
    artists = [s.strip() for s in artists.split(',')]

    # Remove any bracket expressions [...]
    title = re_bracket.sub('', title).strip()

    return artists, title


def filter_entries(entries, title_pattern: str | None, artist_pattern: str | None):
    # No patterns given
    if not (title_pattern or artist_pattern):
        return entries

    # Every match function will need to be fullfilled to add an entry
    match_functions = []

    if re_title:
        re_title = re.compile(title_pattern)
        def match_title(entry):
            if re_title.search(entry['title']):
                return True
            return False
        match_functions.append(match_title)

    if re_artist:
        re_artist = re.compile(artist_pattern)
        def match_artists(entry):
            for artist in entry['artists']:
                if re_artist.search(artist):
                    return True
            return False
        match_functions.append(match_artists)

    filtered = []
    for entry in entries:
        matched = True
        for match in match_functions:
            if not match(entry):
                matched = False
                break
        
        if matched:
            filtered.append(entry)
        
    return filtered