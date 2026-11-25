"""Tests for tools/install_sequences.py."""

import importlib.util
import os

import pytest

import sequence_select

pytestmark = pytest.mark.unit

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "install_sequences.py")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("install_sequences", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_bundle(root, song="carol", variants=None):
    variants = variants or {"onset_bands": "0,255,0\n", "fft": "1,2,3\n", "stems": "255,0,255\n"}
    bundle = root / "out" / song
    for name, body in variants.items():
        d = bundle / "full" / name
        d.mkdir(parents=True)
        (d / "full.csv").write_text(body)
        (bundle / "clip" / name).mkdir(parents=True)
        (bundle / "clip" / name / "clip.csv").write_text(body)
    return bundle


def test_installs_all_variants(tool, tmp_path, capsys):
    bundle = make_bundle(tmp_path)
    dest = tmp_path / "sequences"
    assert tool.main([str(bundle), "--dest", str(dest), "--channels", "3"]) == 0
    assert sorted(os.listdir(dest / "carol")) == ["onset_bands.csv", "research_fft.csv", "stems.csv"]
    assert (dest / "carol" / "stems.csv").read_text() == "255,0,255\n"
    assert "sequence = rotate:research_fft,onset_bands,stems" in capsys.readouterr().out
    # The show finds them
    song = tmp_path / "music" / "carol.mp3"
    assert sequence_select.list_variants(str(dest), str(song)) == \
        ["onset_bands", "research_fft", "stems"]


def test_subset_and_song_rename(tool, tmp_path):
    bundle = make_bundle(tmp_path)
    dest = tmp_path / "sequences"
    assert tool.main([str(bundle), "--dest", str(dest), "--song", "Carol Bells",
                      "--variants", "stems"]) == 0
    assert os.listdir(dest / "Carol Bells") == ["stems.csv"]


def test_channel_mismatch_skipped(tool, tmp_path):
    bundle = make_bundle(tmp_path, variants={"wide": "1,2,3,4\n", "ok": "1,2,3\n"})
    dest = tmp_path / "sequences"
    assert tool.main([str(bundle), "--dest", str(dest), "--channels", "3"]) == 1
    assert os.listdir(dest / "carol") == ["ok.csv"]


def test_existing_kept_unless_force(tool, tmp_path):
    bundle = make_bundle(tmp_path, variants={"a": "1,2,3\n"})
    dest = tmp_path / "sequences"
    (dest / "carol").mkdir(parents=True)
    existing = dest / "carol" / "a.csv"
    existing.write_text("9,9,9\n")
    assert tool.main([str(bundle), "--dest", str(dest)]) == 1
    assert existing.read_text() == "9,9,9\n"
    assert tool.main([str(bundle), "--dest", str(dest), "--force"]) == 0
    assert existing.read_text() == "1,2,3\n"
    # identical: fine
    assert tool.main([str(bundle), "--dest", str(dest)]) == 0


def test_dry_run_writes_nothing(tool, tmp_path):
    bundle = make_bundle(tmp_path)
    dest = tmp_path / "sequences"
    assert tool.main([str(bundle), "--dest", str(dest), "--dry-run"]) == 0
    assert not dest.exists()


def test_missing_variant_and_empty_bundle(tool, tmp_path):
    bundle = make_bundle(tmp_path)
    dest = tmp_path / "sequences"
    assert tool.main([str(bundle), "--dest", str(dest), "--variants", "nope"]) == 1
    empty = tmp_path / "out" / "empty"
    empty.mkdir()
    assert tool.main([str(empty), "--dest", str(dest)]) == 1


def test_song_needs_single_bundle(tool, tmp_path):
    with pytest.raises(SystemExit):
        tool.main(["a", "b", "--song", "x"])
