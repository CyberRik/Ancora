"""A tiny in-process metrics registry with Prometheus text exposition (Phase 4c).

Ancora deliberately does not pull in ``prometheus_client``: the counters we
expose are a handful of families in plain dicts, and the exposition format is a
few lines. This module is the shared version of the hand-rolled renderer the
scheduler already uses, so the API and the event consumer expose ``/metrics`` the
same way.

Metrics are labelled counters and gauges. A :class:`Counter` only goes up; a
:class:`Gauge` can be set or moved. Both are cheap and thread-safe enough for the
single-writer-per-family use here (FastAPI's event loop, the consumer's loops).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

_LabelKey = tuple[tuple[str, str], ...]


def _key(labels: Mapping[str, str]) -> _LabelKey:
    # Sorted so the same label set always maps to one series regardless of order.
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(key: _LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in key)
    return f"{{{inner}}}"


class _Metric:
    def __init__(self, name: str, kind: str, help_text: str) -> None:
        self.name = name
        self.kind = kind
        self.help = help_text
        self._samples: dict[_LabelKey, float] = {}
        self._lock = threading.Lock()

    def _add(self, value: float, labels: Mapping[str, str]) -> None:
        key = _key(labels)
        with self._lock:
            self._samples[key] = self._samples.get(key, 0.0) + value

    def _set(self, value: float, labels: Mapping[str, str]) -> None:
        key = _key(labels)
        with self._lock:
            self._samples[key] = value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]
        with self._lock:
            samples = sorted(self._samples.items())
        # A family with no observations still emits its HELP/TYPE header so the
        # metric exists in Prometheus from the first scrape (with an implicit 0).
        lines.extend(f"{self.name}{_render_labels(key)} {value}" for key, value in samples)
        return lines


class Counter(_Metric):
    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, "counter", help_text)

    def inc(self, value: float = 1.0, **labels: str) -> None:
        self._add(value, labels)


class Gauge(_Metric):
    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, "gauge", help_text)

    def set(self, value: float, **labels: str) -> None:
        self._set(value, labels)

    def inc(self, value: float = 1.0, **labels: str) -> None:
        self._add(value, labels)

    def dec(self, value: float = 1.0, **labels: str) -> None:
        self._add(-value, labels)


class MetricsRegistry:
    """Holds metric families and renders them in Prometheus text format."""

    def __init__(self) -> None:
        self._metrics: list[_Metric] = []

    def counter(self, name: str, help_text: str) -> Counter:
        metric = Counter(name, help_text)
        self._metrics.append(metric)
        return metric

    def gauge(self, name: str, help_text: str) -> Gauge:
        metric = Gauge(name, help_text)
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        blocks: Iterable[str] = ("\n".join(m.render()) for m in self._metrics)
        return "\n".join(blocks) + "\n"


# The exposition content type Prometheus expects for the text format.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
