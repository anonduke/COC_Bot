"""Compare two snapshots of the same village.

Used to verify actions ("did the Archer Tower 18 upgrade really start?") and
to explain drift between sessions (manual play, helpers, raids don't show here).

For every (section, object) the entries are counted per (level, upgrading).
Count changes are then explained, in this order:
    started   one copy at level L went from idle to upgrading
    finished  an upgrade from L completed: one fewer upgrading at L, one more
              idle at L+1
    leveled   one idle copy went from L to L+1 with no timer seen in between
              (instant upgrades such as walls, or a whole upgrade that
              started and finished between the two snapshots)
Whatever is left is reported as `unexplained` (new buildings, multi-level
jumps, removed obstacles ...), never dropped.
"""
from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Change:
    section: str
    data: int
    level: int | None   # started/finished/leveled: the level it moved FROM
    count: int = 1


@dataclass
class Diff:
    started: list = field(default_factory=list)
    finished: list = field(default_factory=list)
    leveled: list = field(default_factory=list)
    unexplained: list = field(default_factory=list)  # of Residual

    @property
    def empty(self):
        return not (self.started or self.finished or self.leveled or self.unexplained)


@dataclass(frozen=True)
class Residual:
    section: str
    data: int
    level: int | None
    upgrading: bool
    delta: int        # new count - old count


def _counts(snapshot):
    c = Counter()
    for e in snapshot.entries:
        c[(e.section, e.data, e.level, e.upgrading)] += e.count
    return c


def diff(old, new):
    if old.tag != new.tag:
        raise ValueError(f"different villages: {old.tag} vs {new.tag}")
    delta = _counts(new)
    delta.subtract(_counts(old))
    by_obj = {}
    for (section, data, level, upg), d in delta.items():
        if d:
            by_obj.setdefault((section, data), {})[(level, upg)] = d

    out = Diff()
    for (section, data), d in sorted(by_obj.items()):
        levels = sorted({lvl for lvl, _ in d if lvl is not None})

        def take(kind, a, b):
            """Match a decrease at key a with an increase at key b."""
            n = min(-d.get(a, 0), d.get(b, 0))
            if n > 0:
                d[a] = d.get(a, 0) + n
                d[b] -= n
                getattr(out, kind).append(Change(section, data, a[0], n))

        for lvl in levels:
            take("started", (lvl, False), (lvl, True))
        for lvl in levels:
            take("finished", (lvl, True), (lvl + 1, False))
        for lvl in levels:
            take("leveled", (lvl, False), (lvl + 1, False))
        for (lvl, upg), n in sorted(d.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1])):
            if n:
                out.unexplained.append(Residual(section, data, lvl, upg, n))
    return out
