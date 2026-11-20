"""Brand profiles: saved per client, learned from material, folded into the cut's brief."""
import pytest

from theodore import brand


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    from theodore import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)


def test_update_merges_and_prompt_text_lists_the_rules():
    brand.update("Acme Co", audience="new parents", tone="warm, funny", must_say=["Free shipping"])
    brand.update("Acme Co", avoid=["medical claims"])
    p = brand.load("acme co")
    assert p["audience"] == "new parents" and p["avoid"] == ["medical claims"]
    text = brand.prompt_text("Acme Co")
    assert "warm, funny" in text and "Free shipping" in text and "medical claims" in text
    assert brand.prompt_text("nobody") == "" and brand.prompt_text(None) == ""
    with pytest.raises(ValueError):
        brand.update("Acme Co", colour="red")


def test_learn_reads_text_collects_videos_and_reports_what_is_missing(tmp_path, monkeypatch):
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "guide.md").write_text("We speak plainly. Never say cheap.")
    (folder / "site.html").write_text("<html><script>x()</script><p>Built for builders</p></html>")
    (folder / "spot.mov").write_bytes(b"")
    (folder / "logo.psd").write_bytes(b"")
    seen = {}

    def fake_call_json(client, *, user, **kw):
        seen["user"] = user
        return {"audience": "builders", "tone": "plain", "avoid": ["cheap"], "must_say": [],
                "missing": ["Who is the call to action aimed at?"]}

    monkeypatch.setattr(brand, "call_json", fake_call_json)
    out = brand.learn("Acme", [str(folder)], brief="Spring campaign", claude=object())
    assert "Never say cheap" in seen["user"] and "Built for builders" in seen["user"] and "x()" not in seen["user"]
    assert "Spring campaign" in seen["user"]
    assert out["still_missing"] == ["Who is the call to action aimed at?"]
    assert out["videos_to_study"][0].endswith("spot.mov") and out["skipped"][0].endswith("logo.psd")
    assert brand.load("Acme")["avoid"] == ["cheap"]
    with pytest.raises(ValueError):
        brand.learn("Empty", [])


def test_story_cut_tool_folds_the_brand_into_the_brief():
    from theodore.agent import tools
    brand.update("Acme", tone="warm")
    assert "warm" in tools._brand_brief("30s spot", "Acme") and tools._brand_brief("x", None) == "x"
    with pytest.raises(tools.ToolError):
        tools._brand_brief("x", "unknown client")
