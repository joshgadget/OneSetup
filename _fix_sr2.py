import pathlib
f = pathlib.Path(r"C:\Users\Hi\trading\engine\modules\support_resistance.py")
t = f.read_text(encoding="utf-8")

old_dataclass = '''    near_crossings: int = 0
    far_crossings: int = 0
    touch_indices: list[int] = field(default_factory=list)'''
new_dataclass = '''    near_crossings: int = 0
    far_crossings: int = 0
    bounces_count: int = 0
    penetrations_count: int = 0
    entry_counted: bool = False
    touch_indices: list[int] = field(default_factory=list)'''
assert old_dataclass in t
t = t.replace(old_dataclass, new_dataclass, 1)

old_props = '''    @property
    def bounces(self) -> int:
        """S_b = floor(1/2 * crossings of the near edge)."""
        return self.near_crossings // 2

    @property
    def penetrations(self) -> int:
        """The same rule on the far edge."""
        return self.far_crossings // 2'''
new_props = '''    @property
    def bounces(self) -> int:
        """Completed bounces: price entered from the near side and left through the
        near edge without reaching the far edge.

        The spec's prose definition. Its crossing formula
        (S_b = floor(1/2 * crossings of the near edge)) is reported alongside as
        `near_crossings`, but it is not what p(b) means: on simulated random walks the
        crossing form reads about 0.92 because a level anchored on a swing is crossed
        at its near edge far more often than at its far edge, while the event form
        reads about 0.57. Both numbers are reported; see DECISIONS.md D-008.
        """
        return self.bounces_count

    @property
    def penetrations(self) -> int:
        """Completed penetrations: entered from the near side, left through the far edge."""
        return self.penetrations_count'''
assert old_props in t
t = t.replace(old_props, new_props, 1)

old = '''            "near_crossings": self.near_crossings,
            "far_crossings": self.far_crossings,'''
new = '''            "near_crossings": self.near_crossings,
            "far_crossings": self.far_crossings,
            "crossing_ratio": (self.near_crossings / (self.near_crossings + self.far_crossings))
            if (self.near_crossings + self.far_crossings) else None,'''
assert old in t
t = t.replace(old, new, 1)

old_adv = t[t.index("    trigger = False"):t.index("    if atr is not None and atr > 0:\n        buffer = break_buffer_atr * atr")]
new_adv = '''    trigger = False
    near_is_upper = level.kind is LevelKind.SUPPORT
    if near_is_upper:
        # support: the counted episode starts when price comes down into the interval
        if level.position == "above":
            if close <= level.upper:
                level.position = "inside"
                level.entry_counted = True
                level.last_touch_index = index
                level.touch_indices.append(index)
        elif level.position == "inside":
            if close > level.upper:
                trigger = True
                level.position = "above"
                if level.entry_counted:
                    level.bounces_count += 1
                    level.entry_counted = False
            elif close < level.lower:
                level.position = "below"
                if level.entry_counted:
                    level.penetrations_count += 1
                    level.entry_counted = False
        else:  # below: re-entering from underneath does not start a counted episode
            if close > level.upper:
                level.position = "above"
            elif close >= level.lower:
                level.position = "inside"
                level.entry_counted = False
                level.last_touch_index = index
                level.touch_indices.append(index)
    else:
        # resistance: the counted episode starts when price comes up into the interval
        if level.position == "below":
            if close >= level.lower:
                level.position = "inside"
                level.entry_counted = True
                level.last_touch_index = index
                level.touch_indices.append(index)
        elif level.position == "inside":
            if close < level.lower:
                trigger = True
                level.position = "below"
                if level.entry_counted:
                    level.bounces_count += 1
                    level.entry_counted = False
            elif close > level.upper:
                level.position = "above"
                if level.entry_counted:
                    level.penetrations_count += 1
                    level.entry_counted = False
        else:  # above
            if close < level.lower:
                level.position = "below"
            elif close <= level.upper:
                level.position = "inside"
                level.entry_counted = False
                level.last_touch_index = index
                level.touch_indices.append(index)

'''
t = t.replace(old_adv, new_adv, 1)
f.write_text(t, encoding="utf-8", newline="\n")
print("patched")