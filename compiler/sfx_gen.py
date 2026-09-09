"""
Generates a small library of CC0-safe sound effects with plain ffmpeg
synthesis (sine chirps + shaped noise bursts) -- no external audio assets
to source, license, or download. Run once during setup:

    python -m compiler.sfx_gen

build.py calls generate_all() lazily too, so this is self-healing if
sfx/ ever gets wiped.
"""
import logging
import subprocess
from pathlib import Path

import config

log = logging.getLogger("meme_pipeline.sfx_gen")

# name -> ffmpeg lavfi source expression (aevalsrc/anoisesrc) + duration.
# aevalsrc expressions use `t` (seconds) and standard C-style math; each is
# a short amplitude-enveloped chirp or ping so it reads as a "whoosh"/
# "pop"/"ding" without any external sample.
_SPECS = {
    "whoosh_down.wav": (
        'aevalsrc=exprs=\'0.6*sin(PI*t/0.35)*sin(2*PI*(900*t+(150-900)*t*t/(2*0.35)))\':'
        "s=44100:d=0.35"
    ),
    "whoosh_up.wav": (
        'aevalsrc=exprs=\'0.6*sin(PI*t/0.30)*sin(2*PI*(200*t+(1400-200)*t*t/(2*0.30)))\':'
        "s=44100:d=0.30"
    ),
    "pop.wav": (
        'aevalsrc=exprs=\'0.7*exp(-25*t)*sin(2*PI*1100*t)\':s=44100:d=0.18'
    ),
    "ding.wav": (
        'aevalsrc=exprs=\'0.5*exp(-6*t)*sin(2*PI*1760*t)\':s=44100:d=0.30'
    ),
    "click.wav": (
        'anoisesrc=colour=white:duration=0.08:amplitude=0.5'
    ),
}


def _synthesize(name: str, source_expr: str, dest: Path):
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", source_expr,
        "-ac", "2", "-ar", "44100",
    ]
    if name == "click.wav":
        # shape the raw noise burst into a short percussive click
        cmd += ["-af", "highpass=f=1500,afade=t=out:st=0:d=0.08"]
    cmd.append(str(dest))
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def generate_all(force: bool = False):
    config.SFX_DIR.mkdir(parents=True, exist_ok=True)
    for filename, expr in _SPECS.items():
        dest = config.SFX_DIR / filename
        if dest.exists() and not force:
            continue
        log.info("Synthesizing sfx/%s", filename)
        _synthesize(filename, expr, dest)


def list_sfx() -> list[Path]:
    generate_all()
    return sorted(config.SFX_DIR.glob("*.wav"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_all(force=True)
    print(f"Generated {len(list(config.SFX_DIR.glob('*.wav')))} sfx files in {config.SFX_DIR}")
