"""Resize connected component edges without moving their outside boundaries."""
from __future__ import annotations

from .model import Component


class Divider:
    def __init__(self, axis: str, before: Component, after: Component, components: list[Component]):
        self.axis = axis
        position, extent = ("x", "w") if axis == "x" else ("y", "h")
        cross, cross_extent = ("y", "h") if axis == "x" else ("x", "w")
        boundary = getattr(after, position)
        sides = ([c for c in components if getattr(c, position) + getattr(c, extent) == boundary],
                 [c for c in components if getattr(c, position) == boundary])
        selected = [{before.id}, {after.id}]
        changed = True
        while changed:
            changed = False
            for side in (0, 1):
                for candidate in sides[side]:
                    if candidate.id in selected[side]:
                        continue
                    if any(other.id in selected[1-side]
                           and max(getattr(candidate, cross), getattr(other, cross))
                           < min(getattr(candidate, cross) + getattr(candidate, cross_extent),
                                 getattr(other, cross) + getattr(other, cross_extent))
                           for other in sides[1-side]):
                        selected[side].add(candidate.id)
                        changed = True
        self.before = [c for c in sides[0] if c.id in selected[0]]
        self.after = [c for c in sides[1] if c.id in selected[1]]
        self.members = self.before + self.after
        self.original = {c.id: (getattr(c, position), getattr(c, extent)) for c in self.members}
        self.others = [c for c in components if c.id not in self.original]

    def resize(self, delta: int) -> None:
        position, extent = ("x", "w") if self.axis == "x" else ("y", "h")
        maximum = 12 if self.axis == "x" else 40
        lower = max([3-self.original[c.id][1] for c in self.before]
                    + [self.original[c.id][1]-maximum for c in self.after])
        upper = min([maximum-self.original[c.id][1] for c in self.before]
                    + [self.original[c.id][1]-3 for c in self.after])
        delta = max(lower, min(upper, delta))
        while True:
            for component in self.before:
                origin, size = self.original[component.id]
                setattr(component, position, origin)
                setattr(component, extent, size + delta)
            for component in self.after:
                origin, size = self.original[component.id]
                setattr(component, position, origin + delta)
                setattr(component, extent, size - delta)
            if not delta or not any(c.intersects(other) for c in self.members for other in self.others):
                break
            delta += -1 if delta > 0 else 1
