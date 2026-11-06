# Offline demo walkthrough

The walkthrough is now the documentation site's tutorial, **Your first allocation**:
<https://apmalong.github.io/data-finops/tutorials/first-allocation.html>
(source: `site/tutorials/first-allocation.html`). A test runs its commands on every change
(`tests/integration/test_m5_exit.py`).

The quick way, from a source checkout with `uv sync --extra api --frozen`:

```sh
uv run data-finops demo        # open http://127.0.0.1:8000/
```
