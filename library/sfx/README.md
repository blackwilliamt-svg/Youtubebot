# library/sfx/

Drop any `.mp3` / `.wav` / `.ogg` / `.m4a` file here and the compiler will
consider it for the sound-effect layered on top of every cut in a build.

No tagging required -- files are picked at random. If a filename contains
one of the category names (`fails`, `animals`, `gaming`, `wins`,
`oddly-satisfying`, `mildly-infuriating`), e.g. `fail_boing.mp3`, it's
preferred for cuts into a clip of that category; everything else is
treated as generic and can land on any cut.

If this folder is empty (or has nothing usable for a given cut), the
compiler falls back to a small set of effects it synthesizes itself with
ffmpeg (see `compiler/sfx_gen.py`) -- so builds work fine with nothing
dropped in here at all.
