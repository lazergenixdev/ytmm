# Functionality
- File Types: Music files (only mp3, provides best compression and compatability).
- Single or Multiple Downloads: Should handle multiple downloads at once (parrellized?).
- Download Method: `ytdlp` should be a good option.

# User interation
- Command-Line Arguments:
    - `-S` Sync from database of YT urls to a directory.
    - `-A` Append YT url to database.
    - `-R` Remove item from database.
    - `-o` Set output directory (otherwise default can be used, such as `out/`).

- User Feedback: Should give feedback similar to linux package managers (e.g. `pacman`).

# Error Handling and Recovery
- Failure Scenarios:
    - Network issue/failed download: The download should continue but there should be
        an error that shows for that progress bar and a list on program completion that
        shows all errors that occured for which entries in the database.

    - Resume Downloads: For v1.0 of this utility, this functionality is not needed.

# Performance Considerations
- Speed: Faster downloads should be optimized.
- Resource Usage:
    - Full CPU should be expected.
    - Memory should not exceed ~8 GiB to make this program usable on a range of devices.
    - Bandwidth should not be capped to keep things simple.

# Cross-Platform Compatability
- Operating Systems: Should run on all major platforms, Python should handle this.
- Dependencies: Dependencies should always be minimized. We need `yt-dlp` at least.

# Security
Handled by `yt-dlp`.

# Future Proofing
- Extensibility: New features should be anticipated, but this is not a concern right now
- Maintenance: Maintenance is very important, there should be as much documentation
    as needed to fully understand the program and be able to add features/improvements.
    This project should be available on github publicly so anyone can use/contribute.
