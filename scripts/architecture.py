"""Generate docs/architecture.md, the architecture diagrams, from the code.

Every node, edge and table row comes from a file of the repository: the source
registry, the Python imports, the Dockerfile and the GitHub workflows. Only the
names of the outside services come from SERVICES below.
The pipeline runs this script with --check, so a change to any of them that
is not in the committed page fails the build.

Usage (from the repository root, after `uv sync`):
    uv run python scripts/architecture.py          # rewrite docs/architecture.md
    uv run python scripts/architecture.py --check  # fail if the page is out of date
"""

import ast
import difflib
import re
import sys
from pathlib import Path

import yaml

from tracker.sources import DATA_DIR, SOURCES, events_path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "architecture.md"
APP = ROOT / "src" / "streamlit_app.py"
INGEST = ROOT / "src" / "tracker" / "ingest.py"
COMMAND = "uv run python scripts/architecture.py"

INTRO = f"""\
# Architecture

1. Each source module in `src/tracker/` has two steps: `extract()` downloads
   the files of one open dataset, and `normalize()` maps the stored files to
   the unified schema.
2. `tracker ingest` saves the downloaded files in the raw store, validates the
   mapped events and stores them as `data/events/<source>.csv`, committed to
   the repository, and as a clean Parquet table in the raw store.
3. The Streamlit page `src/streamlit_app.py` reads these files and nothing else.
4. The pipeline refreshes the live sources every day, publishes the app and
   publishes the published tables to two public Hugging Face datasets. The app
   does not read them.

This page is generated from the code by `scripts/architecture.py`: do not edit
it by hand. After a change, run this command and commit the result:

```bash
{COMMAND}
```

The pipeline fails while the page is out of date.
"""

OVERVIEW = (
    "Where the events come from, where they are stored, and where they go. "
    "Rounded boxes are outside the repository. Edge labels name the pipeline "
    "job or the manual step that moves the data."
)
PIPELINE = (
    "The jobs of each workflow in `.github/workflows/`. Jobs run from left to "
    "right: a job starts when the jobs it needs have passed. Dashed jobs run "
    "only when their `if` condition holds. Edge labels name the job outputs "
    "that the next job reads. Secrets and variables are set in the repository "
    "settings (Secrets and variables, Actions). The steps table leaves out the "
    'steps without a name, and shows "a script" for a `run` of several lines.'
)
MODULES = "Python files and their imports: an arrow points to the imported module."

# The service that a workflow step reaches, found by a pattern in its command
# or in its `with` options (one `key: value` per line).
# A step that uses secrets or variables and matches none shows its job name.
SERVICES = {
    r"docker (buildx )?push|^push: true$|imagetools create|docker login|^push-to-registry: true$": "Docker Hub",
    r"RENDER_DEPLOY_HOOK_URL": "Render",
    r"\btracker publish\b": "Hugging Face",
    r"HEARTBEAT_URL": "Heartbeat monitor",
}


def node_id(*parts: str) -> str:
    return re.sub(r"\W+", "_", "_".join(parts)).strip("_")


def label(*lines: str) -> str:
    """Quoted Mermaid label, one line per non-empty argument."""
    codes = {"&": "#amp;", '"': "#quot;", "<": "#lt;", ">": "#gt;"}
    escaped = [re.sub(r'[&"<>]', lambda m: codes[m[0]], line) for line in lines]
    return '"' + "<br/>".join(line for line in escaped if line) + '"'


def plain(text: str) -> str:
    """Text for a Markdown table: escape what Markdown would interpret."""
    return re.sub(r"([\\`*_<>|\[\]])", r"\\\1", text)


def code(text: str) -> str:
    """Code span for a Markdown table, where `|` ends the cell even in code."""
    return "`" + text.replace("|", r"\|") + "`"


def condition(spec: dict) -> str:
    """The `if` of a job or a step as a code span on one line, or ""."""
    # YAML reads `if: false` as a bool, and a block (`if: |`) keeps line breaks.
    return code(" ".join(describe(spec["if"]).split())) if "if" in spec else ""


def table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [header, ("---",) * len(header), *rows]
    return "\n".join("| " + " | ".join(cells) + " |" for cells in lines)


def describe(value) -> str:
    """A YAML value on one line."""
    if isinstance(value, dict):
        return ", ".join(f"{key}: {describe(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(describe(item) for item in value)
    if isinstance(value, bool):
        return str(value).lower()
    return "" if value is None else str(value)


def summary(path: Path) -> str:
    """First line of the module docstring."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return (ast.get_docstring(tree) or "").partition("\n")[0]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def references(spec, kind: str) -> list[str]:
    """Names of the `secrets` or `vars` that a workflow object uses."""
    return sorted(set(re.findall(rf"\b{kind}\.(\w+)", str(spec))))


def needed(spec: dict) -> list[str]:
    """The jobs that a job needs."""
    needs = spec.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def job_outputs(spec, job: str) -> list[str]:
    """Names of the outputs of a needed job that a workflow object reads."""
    pattern = rf"needs\.{re.escape(job)}\.outputs\.([\w-]+)"
    return sorted(set(re.findall(pattern, str(spec))))


def service(step: dict, job_name: str) -> str:
    """The service outside the repository that a step reaches, or ""."""
    options = step.get("with") or {}
    text = "\n".join(
        [step.get("run", ""), *(f"{k}: {str(v).lower()}" for k, v in options.items())]
    )
    for pattern, name in SERVICES.items():
        if re.search(pattern, text, re.MULTILINE):
            return name
    if references(step, "secrets") or references(step, "vars"):
        return job_name
    return ""


def image_paths() -> list[str]:
    """The paths that the Dockerfile copies into the image."""
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    paths: list[str] = []
    for line in lines:
        if not line.startswith("COPY "):
            continue
        words = line.split()[1:]
        if any(word.startswith("--from=") for word in words):
            continue
        files = [word for word in words if not word.startswith("--")]
        paths += [path for path in files[:-1] if path not in paths]
    return paths


def overview(workflows: dict, copied: list[str]) -> list[str]:
    ingest = module_name(INGEST)
    lines = ["flowchart TB"]
    lines += [
        f"  {node_id('source', key)}({label(key, source.kind)})"
        for key, source in SOURCES.items()
    ]
    lines += [
        f"  ingest[{label(ingest)}]",
        f"  events[({label(rel(DATA_DIR) + '/')})]",
        f"  app[{label(module_name(APP))}]",
    ]
    jobs = {
        (file, job): spec
        for file, workflow in workflows.items()
        for job, spec in workflow["jobs"].items()
    }
    steps = [
        (file, job, step)
        for (file, job), spec in jobs.items()
        for step in spec.get("steps", [])
    ]

    edges = []
    for key, source in SOURCES.items():
        runners = [
            jobs[file, job].get("name", job) + " job"
            for file, job, step in steps
            if {key, source.kind}
            & set(re.findall(r"tracker ingest (\w+)", step.get("run", "")))
        ]
        text = label(", ".join(dict.fromkeys(runners)) or "by hand")
        edges.append(f"  {node_id('source', key)} -->|{text}| ingest")
    edges += ["  ingest --> events", "  events --> app"]

    reached = {}
    for file, job, step in steps:
        found = service(step, jobs[file, job].get("name", job))
        if found:
            node = node_id("service", found)
            reached.setdefault((file, job), []).append((step, node))
            lines.append(f"  {node}({label(found)})")
    # A step reaches a service with the image of the app, with the stored
    # events, or with the outputs of a job that reaches another service.
    for (file, job), targets in reached.items():
        spec = jobs[file, job]
        text = label(spec.get("name", job) + " job")
        for step, target in targets:
            run = step.get("run", "")
            inputs = []
            if re.search(r"docker (buildx )?build", run) and any(
                APP.is_relative_to(ROOT / path) for path in copied
            ):
                inputs.append("app")
            if rel(DATA_DIR) in run:
                inputs.append("events")
            for other in needed(spec):
                if job_outputs(step, other):
                    inputs += [node for _, node in reached.get((file, other), [])]
            edges += [f"  {node} -->|{text}| {target}" for node in inputs]
    return list(dict.fromkeys(lines)) + list(dict.fromkeys(edges))


def sources_table() -> str:
    rows = [
        (
            code(key),
            f"[{plain(source.title)}]({source.homepage})",
            plain(source.kind),
            plain(source.licence),
            code(rel(events_path(key))),
        )
        for key, source in SOURCES.items()
    ]
    return table(("Source", "Name", "Kind", "Licence", "Stored as"), rows)


def triggers(file: str, workflow: dict) -> str:
    """The events that start a workflow, as one sentence."""
    # PyYAML follows YAML 1.1, where the `on` key reads as True.
    events = workflow.get("on", workflow.get(True))
    if isinstance(events, str):
        events = [events]
    if isinstance(events, list):
        events = dict.fromkeys(events)
    names = [
        f"`{event}`" + (f" (`{describe(config)}`)" if config else "")
        for event, config in events.items()
    ]
    return f"`{file}` runs on " + ", ".join(names) + "."


def pipeline(workflow: dict) -> list[str]:
    lines = ["flowchart LR", "  classDef conditional stroke-dasharray: 6 4"]
    edges = []
    for job, spec in workflow["jobs"].items():
        style = ":::conditional" if "if" in spec else ""
        lines.append(f"  {node_id('job', job)}[{label(spec.get('name', job))}]{style}")
        for other in needed(spec):
            outputs = ", ".join(job_outputs(spec, other))
            arrow = f"-->|{outputs}|" if outputs else "-->"
            edges.append(f"  {node_id('job', other)} {arrow} {node_id('job', job)}")
    return lines + edges


def jobs_table(workflow: dict) -> str:
    rows = [
        (
            plain(spec.get("name", job)),
            condition(spec),
            ", ".join(
                [f"{code(name)} (secret)" for name in references(spec, "secrets")]
                + [f"{code(name)} (variable)" for name in references(spec, "vars")]
            ),
        )
        for job, spec in workflow["jobs"].items()
    ]
    return table(("Job", "Runs if", "Secrets and variables"), rows)


def steps_table(workflow: dict) -> str:
    rows = []
    for job, spec in workflow["jobs"].items():
        for step in spec.get("steps", []):
            if "name" not in step:
                continue
            command = (step.get("run") or step.get("uses", "")).strip()
            rows.append(
                (
                    plain(spec.get("name", job)),
                    plain(step["name"]),
                    condition(step),
                    "a script" if "\n" in command else code(command),
                )
            )
    return table(("Job", "Step", "Runs if", "Runs"), rows)


def module_name(path: Path) -> str:
    """Dotted name inside a package, file name otherwise."""
    if not (path.parent / "__init__.py").exists():
        return path.name
    parts = path.relative_to(ROOT / "src").with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imports(path: Path, internal: set[str]) -> tuple[set[str], set[str]]:
    """Internal modules and third-party packages that a file imports."""
    modules, packages = set(), set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            sys.exit(f"{rel(path)}: relative imports are not supported")
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets = [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        for target in targets:
            parts = target.split(".")
            prefixes = [".".join(parts[:end]) for end in range(len(parts), 0, -1)]
            found = [prefix for prefix in prefixes if prefix in internal]
            if found:
                modules.add(found[0])
            elif parts[0] not in sys.stdlib_module_names:
                packages.add(parts[0])
    return modules, packages


def python_modules() -> list[tuple[str, Path, set[str], set[str]]]:
    """(name, path, internal imports, third-party imports) of each Python file."""
    files = sorted(ROOT.glob("src/**/*.py")) + sorted(ROOT.glob("scripts/*.py"))
    internal = {module_name(path) for path in files}
    found = []
    for path in files:
        imported, packages = imports(path, internal)
        if path.name == "__init__.py" and not imported and not packages:
            continue
        found.append((module_name(path), path, imported, packages))
    return found


def modules(found: list[tuple[str, Path, set[str], set[str]]]) -> list[str]:
    lines = ["flowchart TB"]
    lines += [f"  {node_id('module', name)}[{label(name)}]" for name, *_ in found]
    return lines + [
        f"  {node_id('module', name)} --> {node_id('module', target)}"
        for name, _, imported, _ in found
        for target in sorted(imported)
    ]


def modules_table(found: list[tuple[str, Path, set[str], set[str]]]) -> str:
    rows = [
        (code(name), code(rel(path)), plain(summary(path)), ", ".join(sorted(packages)))
        for name, path, _, packages in found
    ]
    return table(("Module", "File", "Purpose", "Third-party imports"), rows)


def fence(diagram: list[str]) -> str:
    return "\n".join(["```mermaid", *diagram, "```"])


def render() -> str:
    workflows = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    }
    copied = image_paths()
    found = python_modules()
    image = (
        "The Docker image holds "
        + ", ".join(f"`{path}`" for path in copied)
        + ", and the packages of `uv.lock`"
    )
    pipelines = [
        block
        for file, workflow in workflows.items()
        for block in (
            triggers(file, workflow),
            fence(pipeline(workflow)),
            jobs_table(workflow),
            steps_table(workflow),
        )
    ]
    sections = {
        "Overview": [
            f"{OVERVIEW} {image}.",
            fence(overview(workflows, copied)),
            sources_table(),
        ],
        "Pipeline": [PIPELINE, *pipelines],
        "Modules": [MODULES, fence(modules(found)), modules_table(found)],
    }
    parts = [INTRO]
    for title, blocks in sections.items():
        parts.append(f"## {title}\n\n" + "\n\n".join(blocks) + "\n")
    return "\n".join(parts)


def main() -> None:
    text = render()
    if sys.argv[1:] == ["--check"]:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if committed != text:
            sys.stdout.writelines(
                difflib.unified_diff(
                    committed.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    f"{rel(OUTPUT)} (committed)",
                    f"{rel(OUTPUT)} (from the code)",
                )
            )
            sys.exit(f"{rel(OUTPUT)} is out of date. Run: {COMMAND}")
        print(f"{rel(OUTPUT)} is up to date")
    elif sys.argv[1:]:
        sys.exit(__doc__)
    else:
        OUTPUT.parent.mkdir(exist_ok=True)
        OUTPUT.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {rel(OUTPUT)}")


if __name__ == "__main__":
    main()
