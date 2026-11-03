"""Assembling a timeline: every piece lands at exactly the length asked for, or not at all."""
from theodore.resolve.timeline_ops import _place_exact


class Item:
    def __init__(self, start, duration, past_end=0):
        self.start, self.duration, self.past_end = start, duration, past_end

    def GetStart(self):
        return self.start

    def GetDuration(self):
        return self.duration

    def GetEnd(self):
        return self.start + self.duration + self.past_end


class Fake:
    """Stands in for Resolve: `length` decides what a placement really comes out as."""

    def __init__(self, length, past_end=lambda s, e: 0):
        self.length, self.past_end, self.on_track = length, past_end, []

    def AppendToTimeline(self, infos):
        i = infos[0]
        s, e = i["startFrame"], i["endFrame"]
        item = Item(i["recordFrame"], self.length(s, e), self.past_end(s, e))
        self.on_track.append(item)
        return [item]

    def DeleteClips(self, items, ripple):
        for item in items:
            self.on_track.remove(item)
        return True


INFO = {"startFrame": 100, "endFrame": 200, "recordFrame": 5000}


def test_end_frame_one_past_the_length_lands_exact():
    fake = Fake(lambda s, e: e - s - 1)
    got = _place_exact(fake, fake, INFO, 100)
    assert got.GetDuration() == 100 and fake.on_track == [got]


def test_end_frame_that_rounds_differently_still_lands_exact():
    fake = Fake(lambda s, e: e - s)
    assert _place_exact(fake, fake, INFO, 100).GetDuration() == 100


def test_a_length_only_reachable_by_moving_the_in_point():
    fake = Fake(lambda s, e: e - s - 1 if s != 100 else 98)
    got = _place_exact(fake, fake, INFO, 100)
    assert got.GetDuration() == 100 and fake.on_track == [got]


def test_duration_that_reads_right_while_the_clip_runs_a_frame_long_is_rejected():
    fake = Fake(lambda s, e: 100, past_end=lambda s, e: 1 if e == 201 else 0)
    got = _place_exact(fake, fake, INFO, 100)
    assert got.GetEnd() == 5100 and fake.on_track == [got]


def test_unreachable_length_leaves_nothing_on_the_track():
    fake = Fake(lambda s, e: 7)
    assert _place_exact(fake, fake, INFO, 100) is None and fake.on_track == []
