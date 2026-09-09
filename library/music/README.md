# library/music/

Drop royalty-free or Creative Commons background music tracks here
(`.mp3` / `.wav` / `.ogg` / `.m4a`). Nothing is bundled -- source your own
and make sure you're actually clear to use them commercially before a
compilation goes out under your channel.

When a compilation includes a segment with no usable native audio (a
silent video clip, or an image/GIF slide -- those are always silent), the
compiler picks a random track from here, loops it to fill the segment,
and ducks it well under any sound effect or other clip's native audio
(see `MUSIC_DUCK_VOLUME` in `.env`).

If this folder is empty, silent segments just stay silent -- no error.
