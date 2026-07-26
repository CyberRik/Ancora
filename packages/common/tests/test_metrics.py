"""Unit tests for the Prometheus metrics registry (Phase 4c)."""

from __future__ import annotations

from ancora_common.metrics import MetricsRegistry


def test_counter_accumulates_per_label_set() -> None:
    reg = MetricsRegistry()
    started = reg.counter("ancora_runs_started_total", "Runs started, by workflow.")
    started.inc(workflow="hello")
    started.inc(workflow="hello")
    started.inc(workflow="pipeline")
    out = reg.render()
    assert 'ancora_runs_started_total{workflow="hello"} 2.0' in out
    assert 'ancora_runs_started_total{workflow="pipeline"} 1.0' in out


def test_gauge_set_inc_dec() -> None:
    reg = MetricsRegistry()
    active = reg.gauge("ancora_active", "Currently active.")
    active.inc()
    active.inc()
    active.dec()
    assert "ancora_active 1.0" in reg.render()
    active.set(0)
    assert "ancora_active 0" in reg.render()


def test_label_order_is_normalized() -> None:
    reg = MetricsRegistry()
    c = reg.counter("m_total", "help")
    c.inc(b="2", a="1")
    c.inc(a="1", b="2")  # same series regardless of kwarg order
    out = reg.render()
    assert out.count("m_total{") == 1
    assert 'm_total{a="1",b="2"} 2.0' in out


def test_render_includes_help_and_type_headers() -> None:
    reg = MetricsRegistry()
    reg.counter("ancora_x_total", "The X counter.")
    out = reg.render()
    assert "# HELP ancora_x_total The X counter." in out
    assert "# TYPE ancora_x_total counter" in out


def test_label_values_are_escaped() -> None:
    reg = MetricsRegistry()
    c = reg.counter("m_total", "help")
    c.inc(name='a"b\\c')
    assert 'm_total{name="a\\"b\\\\c"} 1.0' in reg.render()
