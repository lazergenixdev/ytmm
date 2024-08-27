from re import compile as regex

re_feat    = regex(r'\(feat\. .*\)')
re_invalid = regex(r'[^ 0-9A-Za-z_]')
re_space   = regex(r'\s+')
re_bracket = regex(r'\[.*\]')
re_garbage = regex(r'\(Official .*\)|\(From .*\)|\(feat\. .*\)')

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


def progress_bar(p: float, w=20):
    p = min(max(p, 0.0), 1.0)
    w = w - 7
    n = int(p*w)
    return f"[{'#'*n}{' '*(w-n)}] {int(p*100):3}%"
