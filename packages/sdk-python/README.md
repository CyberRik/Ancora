# ancora (SDK)

The Ancora authoring SDK.

```python
from ancora import Workflow, workflow, activity

@activity.defn(name="greet")
async def greet(inp: GreetInput) -> GreetOutput:
    return GreetOutput(message=f"Hello, {inp.name}!")

@workflow.defn(name="hello")
class Hello(Workflow):
    @workflow.run
    async def run(self, params: dict) -> dict:
        out = await self.call(greet, GreetInput(name=params["name"]))
        return {"message": out.message}
```

- `workflow` / `activity` — re-exported from the Temporal SDK (`defn`, `run`, `signal`, `query`, …).
- `Workflow` — base class adding `self.call(activity, arg, …)` and `self.gather(...)`.
- `ancora.lint` — best-effort determinism checks (`ancora lint`), per RFC-0001a §1.5.

The built-in node library (LLM/HTTP/Python/DB/Approval), declarative DAG SDK, and
Ray-backed execution arrive in later phases — see
[`docs/IMPLEMENTATION-PLAN.md`](../../docs/IMPLEMENTATION-PLAN.md).
