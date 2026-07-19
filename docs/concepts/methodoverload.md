# Using `methodoverload` in AgentArgus (verified reference)

`methodoverload` is the owner's own library (`pip install methodoverload`,
**v0.1.7**, released 2026-01-20). This page records the **verified** API and
dispatch behaviour — read from the installed source and confirmed empirically —
so AgentArgus uses it *gracefully* (where type-dispatch is genuinely cleaner)
rather than *forcefully*. It supersedes the spec's §4, which is out of date.

Repo: <https://github.com/mohdcodes/pyoverload> · Summary: "Runtime function and
method overloading for Python."

## The real public API (exactly three names)

```python
from methodoverload import overload, OverloadedFunction, NoMatchingOverloadError
```

That is the entire exported surface (`methodoverload.__all__`). **There is no
`OverloadMeta` in the public API.** A metaclass *does* exist in the package
(`methodoverload/metaclass.py`) as an internal alternative, but it is **not
exported and not documented** — so AgentArgus does not use it. The spec's
instruction to `from methodoverload import overload, OverloadMeta` is wrong for
v0.1.7; importing `OverloadMeta` from the top level raises `ImportError`.

## How overloading works

`@overload` decorates multiple same-named callables. At definition time it uses
**frame inspection** (`inspect.currentframe().f_back.f_locals`) to find the
sibling of the same name in the *defining namespace* and merge them into one
`OverloadedFunction` dispatcher. This works identically at module scope and
**inside a class body** — which is precisely why no metaclass is needed for
instance/class/static methods.

### Free functions

```python
@overload
def add(a: int, b: int):
    return a + b

@overload
def add(a: str, b: str):
    return f"{a} {b}"

add(1, 2)          # 3
add("Hello", "World")  # "Hello World"
```

### Instance methods — no metaclass required (verified)

```python
class EvalDataset:
    @overload
    def load(self, source: str):   ...   # path
    @overload
    def load(self, source: list):  ...   # in-memory records
    @overload
    def load(self, source: dict):  ...   # single record
```

`OverloadedFunction.__get__` (the descriptor protocol) binds `self`/`cls`
correctly. `classmethod`/`staticmethod` are supported by placing `@overload`
**outermost**, then `@classmethod`/`@staticmethod`.

## Dispatch semantics (the rules we must design around)

1. **Dispatch is `isinstance(value, annotation)`** on each annotated parameter.
   Parameters named `self`/`cls` are skipped; unannotated parameters are skipped.
2. **Subclasses match their base** — `wrap(inner: BaseAgent)` matches any
   `BaseAgent` subclass. (Verified.) This is why site #3 can dispatch on
   `BaseAgent`.
3. **No generic types.** `list[int]` vs `list[str]` cannot be told apart — only
   the bare origin (`list`) participates in `isinstance`. Never annotate an
   overload with a parameterized generic and expect dispatch on the parameter.
4. **First matching overload wins**, in definition order. Combined with rule 2
   this creates a **subtype ordering trap**: because `bool` is a subclass of
   `int`, if `int` is registered before `bool`, calling with `True` resolves to
   the `int` overload. (Verified: it returned `'int'`.) **Rule for AgentArgus:
   register the most specific type first.**
5. **`NoMatchingOverloadError`** is raised when nothing matches. Catch it
   explicitly where a fallback is offered. It subclasses an internal
   `OverloadError`; import it from `methodoverload`, do **not** redefine it
   (spec §9 agrees).
6. **Caching:** `OverloadedFunction` memoises resolutions by `(name, args,
   kwargs)`. Fine for our sites; be wary in hot inner loops (spec §4 "measure").

## ⚠️ Critical gotcha: `from __future__ import annotations` breaks dispatch

Dispatch does `isinstance(value, param.annotation)` on the **raw** annotation.
`from __future__ import annotations` (PEP 563) turns every annotation into a
**string** (`"BaseAgent"` instead of the class), and `isinstance(x, "BaseAgent")`
raises `TypeError: isinstance() arg 2 must be a type`. `methodoverload` does not
resolve stringized annotations.

**Rule:** any module containing an `@overload` site must **omit** `from
__future__ import annotations`, and the overloaded parameters must carry real
(non-stringized) type annotations. Discovered in Module 1 (`agent.py` drops the
future import for exactly this reason). Verified empirically. This applies to
every planned site: `cost.py` (#2), `dataset.py` (#1), `metrics/base.py` (#4).

## Second gotcha: a plain method OVERWRITES an `@overload`

The library only merges siblings that are **both** `@overload`-decorated (it
finds them via frame inspection of the class namespace). If you write one
`@overload def wrap` followed by a plain `def wrap`, the plain one simply rebinds
the name and the overload is lost — silently. So a "catch-all fallback" must
itself be `@overload`-decorated. Because arbitrary callables have no distinct
`isinstance` class, the catch-all dispatches on **`object`** (which matches
anything) and is registered **after** the specific overload, so first-match-wins
routes correctly. Verified in Module 1's `Agent.wrap`.

## Where AgentArgus uses it (and why it fits)

| Site | Method | Dispatch types | Why overload beats an `isinstance` ladder |
|------|--------|----------------|--------------------------------------------|
| #1 | `EvalDataset.load(source)` | `str` / `list` / `dict` | Three genuinely distinct runtime shapes; open/closed — add a new source type by adding an overload, not editing a branch. |
| #2 | `CostTracker.add_usage(usage)` | `dict` / `Usage` / provider response | Different callers pass different shapes; removes a growing `isinstance` chain. |
| #3 | `Agent.wrap(inner)` | `BaseAgent` (+ fallback) | Subclass dispatch is clean for `BaseAgent`; **callables have no distinct `isinstance` class**, so the plain-callable case is a documented fallback, not an overload (see below). |
| #4 | `Metric.compute(x)` | `RunResult` / `dict` | Lets metrics accept a full result in prod and a lightweight dict trace in unit tests. |

### The honest non-fit: callables (site #3 caveat)

There is no clean `isinstance` class for "an arbitrary callable" (`function`,
`lambda`, a class with `__call__`, a bound method are all just `object`).
Verified: you cannot write `wrap(inner: Callable)` and have it dispatch away
from `object`. So `Agent.wrap` overloads on `BaseAgent` only, and everything
else falls through to a single non-overloaded path that wraps the callable
directly. This is exactly the "do not force methodoverload where it doesn't fit"
guidance in spec §4.3 — and we record *why* rather than pretending it dispatched.
