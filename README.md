# ytmm - Youtube Music Manager

## [Download Standalone (Windows/Linux/Mac)](https://github.com/lazergenixdev/ytmm/releases/latest)

# Commandline
Add music to database
```sh
ytmm add https://www.youtube.com/watch?v=XXXXXXXXXXX
```

Show all music in database
```sh
ytmm query
```
# Embedded Example
```py
import ytmm

urls = some_function_to_get_urls()

with ytmm.YoutubeMM() as mm:
    mm.add(urls)
```
