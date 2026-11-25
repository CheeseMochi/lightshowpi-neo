#!/usr/bin/env python3
"""
Copy a research bundle's variant CSVs to where the show looks for them.

    python tools/install_sequences.py research/out/carol-of-the-bells
    python tools/install_sequences.py research/out/* --channels 7 --variants onset_bands,stems

Each bundle ``research/out/<song>/full/<variant>/full.csv`` becomes
``<dest>/<song>/<variant>.csv`` (dest defaults to
$SYNCHRONIZED_LIGHTS_HOME/music/sequences, the default ``[lightshow]
sequences_dir``). ``<song>`` must match the audio file name without its
extension; use --song to rename when it doesn't.

Variants named like a reserved sequence keyword (``fft``, ``csv``, ``rotate``,
``random``) are installed as ``research_<name>``. With --channels every row is
checked against gpio_len first. Existing files are kept unless they are
identical or --force is given.

Then pick the sequence per song, in the Songs page or in the song's
``.<song-file>.cfg``::

    [custom_lightshow]
    sequence = rotate:onset_bands,stems,fft
"""

import argparse
import filecmp
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'py'))

import sequence_select  # noqa: E402


def default_dest():
    home = os.environ.get('SYNCHRONIZED_LIGHTS_HOME') or REPO
    return os.path.join(home, 'music', 'sequences')


def target_name(variant):
    """Installed variant name: reserved words get a ``research_`` prefix."""
    return 'research_' + variant if variant in sequence_select.RESERVED else variant


def bundle_variants(bundle):
    """{variant: full.csv path} for every variant in a bundle (sorted)."""
    full = os.path.join(bundle, 'full')
    found = {}
    try:
        names = sorted(os.listdir(full))
    except OSError:
        return found
    for name in names:
        csv_path = os.path.join(full, name, 'full.csv')
        if os.path.isfile(csv_path):
            found[name] = csv_path
    return found


def copy_atomic(src, dst):
    folder = os.path.dirname(dst)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.tmp-')
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def install_bundle(bundle, dest, song=None, variants=None, channels=None,
                   force=False, dry_run=False, out=print):
    """Install one bundle; returns (installed names, number of problems)."""
    bundle = os.path.normpath(bundle)
    stem = song or os.path.basename(bundle)
    available = bundle_variants(bundle)
    if not available:
        out(f"{bundle}: no full/<variant>/full.csv found, skipped")
        return [], 1

    wanted = variants or list(available)
    problems = 0
    installed = []
    folder = os.path.join(dest, stem)
    for variant in wanted:
        src = available.get(variant)
        if src is None:
            out(f"{stem}: no variant '{variant}' in {bundle}")
            problems += 1
            continue
        name = target_name(variant)
        if not sequence_select.is_valid_variant_name(name):
            out(f"{stem}: variant name '{variant}' can't be used as a file name, skipped")
            problems += 1
            continue
        if channels:
            problem = sequence_select.check_csv(src, channels, full=True)
            if problem:
                out(f"{stem}/{variant}: {problem}, skipped")
                problems += 1
                continue
        dst = os.path.join(folder, name + '.csv')
        if os.path.exists(dst):
            if filecmp.cmp(src, dst, shallow=False):
                out(f"{stem}/{name}: already installed")
                installed.append(name)
                continue
            if not force:
                out(f"{stem}/{name}: {dst} exists and differs (use --force to replace)")
                problems += 1
                continue
        note = f" (from '{variant}')" if name != variant else ''
        if dry_run:
            out(f"{stem}/{name}: would copy {src} -> {dst}{note}")
        else:
            os.makedirs(folder, exist_ok=True)
            copy_atomic(src, dst)
            out(f"{stem}/{name}: installed {dst}{note}")
        installed.append(name)
    return installed, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bundles', nargs='+', help='research/out/<song> folders')
    parser.add_argument('--dest', default=default_dest(),
                        help='sequences folder (default: %(default)s)')
    parser.add_argument('--song', help='song name (audio file name without extension) '
                                       'when it differs from the bundle folder; one bundle only')
    parser.add_argument('--variants', help='comma-separated subset to install (default: all)')
    parser.add_argument('--channels', type=int,
                        help="check every row has this many columns (the Pi's gpio_len)")
    parser.add_argument('--force', action='store_true', help='replace differing files')
    parser.add_argument('--dry-run', action='store_true', help='show what would be copied')
    args = parser.parse_args(argv)

    if args.song and len(args.bundles) > 1:
        parser.error('--song works with one bundle at a time')
    variants = [v.strip() for v in args.variants.split(',') if v.strip()] if args.variants else None

    total_problems = 0
    for bundle in args.bundles:
        installed, problems = install_bundle(bundle, args.dest, args.song, variants,
                                             args.channels, args.force, args.dry_run)
        total_problems += problems
        if installed:
            stem = args.song or os.path.basename(os.path.normpath(bundle))
            print(f"{stem}: set in its .cfg [custom_lightshow]: "
                  f"sequence = rotate:{','.join(installed)}")
    return 1 if total_problems else 0


if __name__ == '__main__':
    sys.exit(main())
