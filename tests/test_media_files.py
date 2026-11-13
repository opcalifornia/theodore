from theodore.cli import _media_files


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_single_file_source_returns_just_that_file(tmp_path):
    f = tmp_path / "clip.mov"
    touch(f)
    assert _media_files(f) == [f]


def test_finds_files_directly_in_the_source_folder(tmp_path):
    touch(tmp_path / "a.mov")
    touch(tmp_path / "b.wav")
    touch(tmp_path / "notes.txt")
    assert _media_files(tmp_path) == [tmp_path / "a.mov", tmp_path / "b.wav"]


def test_finds_files_in_subfolders_recursively(tmp_path):
    # The real-world case that broke: media organized into per-type or
    # per-card subfolders rather than sitting flat in the chosen folder.
    touch(tmp_path / "Recap Interviews" / "R046_C024.mxf")
    touch(tmp_path / "Recap Interviews" / "R046_C027.mxf")
    touch(tmp_path / "Lav Mics" / "DJI_Audio_001.wav")

    found = _media_files(tmp_path)

    assert found == sorted([
        tmp_path / "Lav Mics" / "DJI_Audio_001.wav",
        tmp_path / "Recap Interviews" / "R046_C024.mxf",
        tmp_path / "Recap Interviews" / "R046_C027.mxf",
    ])


def test_finds_files_nested_several_levels_deep(tmp_path):
    touch(tmp_path / "Card1" / "2026-08-23" / "clip.r3d")
    assert _media_files(tmp_path) == [tmp_path / "Card1" / "2026-08-23" / "clip.r3d"]


def test_extension_matching_is_case_insensitive(tmp_path):
    touch(tmp_path / "CLIP.MOV")
    assert _media_files(tmp_path) == [tmp_path / "CLIP.MOV"]


def test_appledouble_sidecar_files_are_excluded(tmp_path):
    # macOS writes a hidden "._name.ext" resource-fork sidecar next to every
    # real file on exFAT/FAT32 drives -- same extension, a few KB, not media.
    touch(tmp_path / "clip.mov")
    touch(tmp_path / "._clip.mov")
    assert _media_files(tmp_path) == [tmp_path / "clip.mov"]


def test_non_media_extensions_are_never_matched(tmp_path):
    touch(tmp_path / "readme.txt")
    touch(tmp_path / "thumbs.db")
    touch(tmp_path / ".DS_Store")
    assert _media_files(tmp_path) == []


def test_empty_folder_returns_nothing(tmp_path):
    assert _media_files(tmp_path) == []
