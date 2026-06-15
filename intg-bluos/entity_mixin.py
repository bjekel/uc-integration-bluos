"""Shared mixin for UC entity classes."""

from typing import Any


class DiffPushMixin:
    """
    Mixin that tracks a _force_update flag and provides diff-before-push helpers.

    Entities mix this in to avoid duplicating the same three methods across every
    entity class (media_player, select_entity, sensor_entity, remote_entity).

    Subclasses must call super().__init__() so _force_update is initialised before
    the ucapi base class constructor touches self.attributes.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._force_update: bool = False
        super().__init__(*args, **kwargs)

    def _diff_attributes(self, computed: dict[str, Any]) -> dict[str, Any]:
        """
        Return the subset of ``computed`` that differs from ``self.attributes``.

        When ``_force_update`` is set, every computed value is returned and the
        flag is reset, forcing a full resync to the Remote.
        """
        if self._force_update:
            self._force_update = False
            changed = dict(computed)
        else:
            changed = {key: value for key, value in computed.items() if self.attributes.get(key) != value}
        self.attributes.update(changed)
        return changed

    def clear_cached_attributes(self) -> None:
        """
        Force the next update_attributes() call to push all values.

        Used after a (re)subscribe or standby exit, when the Remote may have
        dropped our state.
        """
        self._force_update = True
