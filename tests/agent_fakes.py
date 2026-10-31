"""Fakes for the agent tests: a Resolve that behaves enough like the real one
(timelines register on duplicate, frames export to disk, renders queue) and a
scripted stand-in for the Anthropic client."""
from __future__ import annotations

from types import SimpleNamespace

from fake_resolve import (
    FakeAssemblyTimeline, FakeMediaPool, FakeMediaPoolItem, FakeProject,
    FakeResolve, FakeTimelineItem,
)
from theodore.resolve.connection import ResolveHandles

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


class AgentTimeline(FakeAssemblyTimeline):
    def __init__(self, name, project=None, **kw):
        super().__init__(name, **kw)
        self.project = project
        self.timecode = None

    def SetCurrentTimecode(self, tc):
        self.timecode = tc
        return True

    def DuplicateTimeline(self, name=None):
        dup = super().DuplicateTimeline(name)
        if dup and dup is not False and not isinstance(dup, str):
            dup.__class__ = AgentTimeline
            dup.project, dup.timecode = self.project, None
            self.project.timelines.append(dup)
        return dup


class AgentPool(FakeMediaPool):
    def DeleteTimelines(self, timelines):
        for t in timelines:
            self.project.timelines.remove(t)
        return True


class AgentProject(FakeProject):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.media_pool = AgentPool(self)
        self.presets = ["H.264 Master", "Vertical 9:16"]
        self.jobs, self.rendering, self.render_settings = [], False, {}
        self.export_ok = True
        self.export_dir_writes = 0

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def ExportCurrentFrameAsStill(self, path):
        if not self.export_ok:
            return False
        open(path, "wb").write(PNG)
        return True

    def GetRenderPresetList(self):
        return self.presets

    def LoadRenderPreset(self, name):
        return name in self.presets

    def SetRenderSettings(self, s):
        self.render_settings = s
        return True

    def AddRenderJob(self):
        self.jobs.append(f"job{len(self.jobs) + 1}")
        return self.jobs[-1]

    def StartRendering(self, *ids):
        self.rendering = True
        return True

    def IsRenderingInProgress(self):
        return self.rendering

    def GetRenderJobStatus(self, job):
        return {"JobStatus": "Rendering"}


def make_resolve():
    project = AgentProject()
    tl = AgentTimeline("Interview v1", project=project)
    item = FakeTimelineItem(FakeMediaPoolItem(name="A001.mov"), start=86400, duration=480)
    tl.tracks["video"][1] = [item]
    project.timelines.append(tl)
    project.current_timeline = tl
    project.media_pool.root.clips.append(item.media_pool_item) if hasattr(project.media_pool, "root") else None
    resolve = FakeResolve(project)
    return resolve, project, tl


def connect_factory(resolve, project):
    def connect(**_options):  # require_project / require_timeline / agent
        return ResolveHandles(
            resolve=resolve, project_manager=resolve.project_manager, project=project,
            timeline=project.current_timeline, via="fake",
            info={"product": "DaVinci Resolve Studio", "version": "21.0.4", "studio": True},
        )
    return connect


# --- scripted Claude --------------------------------------------------------

def text(t):
    return SimpleNamespace(type="text", text=t)


_n = [0]


def call(_tool, **inp):
    _n[0] += 1
    return SimpleNamespace(type="tool_use", id=f"tu{_n[0]}", name=_tool, input=inp)


def reply(*blocks):
    return SimpleNamespace(content=list(blocks), usage=SimpleNamespace(input_tokens=10, output_tokens=5))


class ScriptedClient:
    """Plays back prepared responses and records every request, so a test can
    assert on exactly what the model was shown."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.messages = self

    def create(self, **kw):
        import copy
        self.requests.append(copy.deepcopy(kw))
        if not self.responses:
            raise AssertionError("the agent asked for more responses than the script has")
        return self.responses.pop(0)
