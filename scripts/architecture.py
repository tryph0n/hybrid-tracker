"""Generate docs/architecture.md, the architecture diagrams, from the code.

Every node and edge comes from a file of the repository: the source registry,
data/events/, the Python imports, the Dockerfile and the GitHub workflows.
The pipeline runs this script with --check, so a change to any of them that
is not in the committed page fails the build.

Usage (from the repository root, with PYTHONPATH=src):
    python scripts/architecture.py          # rewrite docs/architecture.md
    python scripts/architecture.py --check  # fail if the page is out of date
"""

import ast
import difflib
import re
import sys
from pathlib import Path

import yaml

from tracker.sources import DATA_DIR, SOURCES

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "architecture.md"
APP = ROOT / "src" / "streamlit_app.py"
INGEST = ROOT / "src" / "tracker" / "ingest.py"
COMMAND = "PYTHONPATH=src python scripts/architecture.py"

INTRO = f"""\
# Architecture

1. Each source module in `src/tracker/` downloads one open dataset and maps it
   to the unified schema.
2. `python -m tracker.ingest` validates the result and stores it as
   `data/events/<source>.csv`, committed to the repository.
3. The Streamlit page `src/streamlit_app.py` reads these files and nothing else.
4. The pipeline refreshes the live sources every day and publishes the app.

This page is generated from the code by `scripts/architecture.py`: do not edit
it by hand. After a change, run this command and commit the result:

```bash
{COMMAND}
```

The pipeline fails while the page is out of date.
"""

# Mermaid wraps label lines at 200 px by default, which splits most lines here.
# Flow style on one line: Sphinx (MyST) reads this block as the directive
# option :config:, which sphinxcontrib-mermaid parses as JSON.
FRONT_MATTER = """\
---
config: {"flowchart": {"wrappingWidth": 400}}
---"""

OVERVIEW = (
    "Where the events come from, where they are stored, and how the app is "
    "published. Edge labels name the workflow steps that do the work."
)
PIPELINE = (
    "Each workflow in `.github/workflows/`: its triggers, settings and jobs, then "
    "the steps in order. Dashed steps run only when their `if` condition holds. "
    "Dotted arrows point to the secrets and variables a step uses: they are "
    "set in the repository settings."
)
MODULES = (
    "Python files and their imports: an arrow points to the imported module. "
    "Each box gives the module docstring and its third-party imports."
)


def node_id(*parts: str) -> str:
    return re.sub(r"\W+", "_", "_".join(parts)).strip("_")


def label(*lines: str) -> str:
    """Quoted Mermaid label, one line per non-empty argument."""
    codes = {"&": "#amp;", '"': "#quot;", "<": "#lt;", ">": "#gt;"}
    escaped = [re.sub(r'[&"<>]', lambda m: codes[m[0]], line) for line in lines]
    return '"' + "<br/>".join(line for line in escaped if line) + '"'


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


def load_steps(workflows: dict) -> list[dict]:
    """Every step of every job, with a unique node id."""
    steps, seen = [], {}
    for file, workflow in workflows.items():
        for job, spec in workflow["jobs"].items():
            for step in spec.get("steps", []):
                title = step.get("name") or step.get("uses") or step["run"]
                key = node_id("step", title)
                seen[key] = seen.get(key, 0) + 1
                steps.append(
                    {
                        "id": key if seen[key] == 1 else f"{key}_{seen[key]}",
                        "file": file,
                        "job": job,
                        "title": title,
                        "spec": step,
                    }
                )
    return steps


def output_edges(steps: list[dict], shown: list[dict]) -> list[str]:
    """Edges from a shown step to the shown steps that read its outputs."""
    edges = []
    for step in shown:
        for source, output in re.findall(
            r"steps\.([\w-]+)\.outputs\.([\w-]+)", str(step["spec"])
        ):
            for producer in steps:
                if (
                    producer in shown
                    and producer["job"] == step["job"]
                    and producer["spec"].get("id") == source
                ):
                    edges.append(f"  {producer['id']} -.->|{output}| {step['id']}")
    return list(dict.fromkeys(edges))


def overview(steps: list[dict]) -> list[str]:
    lines = ["flowchart TB"]
    lines.append(f"  subgraph sources[{label('Open datasets: tracker.sources')}]")
    for key, source in SOURCES.items():
        module = sys.modules[source.fetch.__module__]
        urls = [source.homepage] + [
            value
            for value in vars(module).values()
            if isinstance(value, str)
            and re.match(r"https?://", value)
            and value != source.homepage
        ]
        text = label(source.title, f"{key}: {source.kind}, {source.licence}", *urls)
        lines.append(f"    {node_id('source', key)}[{text}]")
    lines.append("  end")

    lines.append(f"  ingest[{label('tracker.ingest', summary(INGEST))}]")
    for key, source in SOURCES.items():
        runners = [
            f"{step['file']}: {step['title']}"
            for step in steps
            if {key, source.kind}
            & set(re.findall(r"tracker\.ingest (\w+)", step["spec"].get("run", "")))
        ]
        text = label(", ".join(runners) or f"by hand: tracker.ingest {source.kind}")
        lines.append(f"  {node_id('source', key)} -- {text} --> ingest")

    lines.append(f"  subgraph events[{label(rel(DATA_DIR) + '/')}]")
    for path in sorted(DATA_DIR.glob("*.csv")):
        lines.append(f"    {node_id('events', path.stem)}[({label(path.name)})]")
    lines.append("  end")
    lines += [f"  ingest --> {node_id('events', key)}" for key in SOURCES]
    lines.append(f"  app[{label(rel(APP), summary(APP))}]")
    lines.append("  events --> app")

    instructions = {}
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            keyword, _, args = line.partition(" ")
            instructions.setdefault(keyword, []).append(args)
    copied = [path for args in instructions["COPY"] for path in args.split()[:-1]]
    text = label(
        "Docker image: Dockerfile",
        "FROM " + ", ".join(instructions["FROM"]),
        "COPY " + ", ".join(copied),
        "EXPOSE " + ", ".join(instructions.get("EXPOSE", [])),
    )
    lines.append(f"  image[[{text}]]")
    for path in copied:
        if APP.is_relative_to(ROOT / path):
            lines.append("  app --> image")
        if DATA_DIR.is_relative_to(ROOT / path):
            lines.append("  events --> image")

    delivery = [
        step
        for step in steps
        if references(step["spec"], "secrets") or references(step["spec"], "vars")
    ]
    title = label("Workflow steps that use secrets or variables")
    lines.append(f"  subgraph delivery[{title}]")
    for step in delivery:
        names = [
            f"{kind}.{name}"
            for kind in ("vars", "secrets")
            for name in references(step["spec"], kind)
        ]
        text = label(f"{step['file']}: {step['title']}", "uses " + ", ".join(names))
        lines.append(f"    {step['id']}([{text}])")
    lines.append("  end")
    for step in delivery:
        if re.search(r"docker (buildx )?(build|push)", step["spec"].get("run", "")):
            lines.append(f"  image --> {step['id']}")
    return lines + output_edges(steps, delivery)


def step_node(step: dict) -> str:
    spec = step["spec"]
    shown = ("name", "uses", "with", "run", "env", "id", "if")
    text = label(
        step["title"],
        f"uses: {spec['uses']}" if "name" in spec and "uses" in spec else "",
        *(f"{key}: {describe(value)}" for key, value in spec.get("with", {}).items()),
        *(
            "runs " + (module or script)
            for module, script in re.findall(
                r"-m ([\w.]+(?: \w+)?)|((?:scripts|src)/[\w/]+\.py)",
                spec.get("run", ""),
            )
        ),
        *(
            f"{key}: {describe(value)}"
            for key, value in spec.items()
            if key not in shown
        ),
        f"if: {spec['if']}" if "if" in spec else "",
    )
    return f"    {step['id']}[{text}]" + (":::conditional" if "if" in spec else "")


def pipeline(workflows: dict, steps: list[dict]) -> list[str]:
    lines = ["flowchart TB", "  classDef conditional stroke-dasharray: 6 4"]
    used = {}
    for file, workflow in workflows.items():
        flow = node_id("workflow", file)
        lines.append(f"  subgraph {flow}_box[{label('.github/workflows/' + file)}]")
        # PyYAML follows YAML 1.1, where the `on` key reads as True.
        triggers = workflow.get("on", workflow.get(True))
        if isinstance(triggers, str):
            triggers = [triggers]
        if isinstance(triggers, list):
            triggers = dict.fromkeys(triggers)
        for event, config in triggers.items():
            trigger = node_id("on", file, event)
            lines.append(f"    {trigger}([{label(event, describe(config))}])")
            lines.append(f"    {trigger} --> {flow}")
        settings = [
            f"{key}: {describe(value)}"
            for key, value in workflow.items()
            if key not in ("name", "on", True, "jobs")
        ]
        text = label("workflow " + workflow.get("name", file), *settings)
        lines.append(f"    {flow}[{text}]")
        for job, spec in workflow["jobs"].items():
            job_node = node_id("job", file, job)
            details = [
                f"{key}: {describe(value)}"
                for key, value in spec.items()
                if key not in ("name", "steps")
            ]
            text = label("job " + spec.get("name", job), *details)
            lines.append(f"    {job_node}[{text}]")
            needs = spec.get("needs", [])
            for needed in [needs] if isinstance(needs, str) else needs:
                lines.append(f"    {node_id('job', file, needed)} --> {job_node}")
            if not needs:
                lines.append(f"    {flow} --> {job_node}")
            previous = job_node
            for step in steps:
                if step["file"] == file and step["job"] == job:
                    lines.append(step_node(step))
                    lines.append(f"    {previous} --> {step['id']}")
                    previous = step["id"]
                    for kind in ("secrets", "vars"):
                        for name in references(step["spec"], kind):
                            used.setdefault((kind, name), []).append(step["id"])
        lines.append("  end")
    lines += output_edges(steps, steps)

    if used:
        title = label("Repository settings: Secrets and variables, Actions")
        lines.append(f"  subgraph settings[{title}]")
        for kind, name in sorted(used):
            if kind == "secrets":
                lines.append(
                    f"    {node_id(kind, name)}{{{{{label('secret ' + name)}}}}}"
                )
            else:
                lines.append(
                    f"    {node_id(kind, name)}[/{label('variable ' + name)}/]"
                )
        lines.append("  end")
        for (kind, name), users in sorted(used.items()):
            lines += [f"  {user} -.-> {node_id(kind, name)}" for user in users]
    return lines


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


def modules() -> list[str]:
    files = sorted(ROOT.glob("src/**/*.py")) + sorted(ROOT.glob("scripts/*.py"))
    internal = {module_name(path) for path in files}
    folders, edges = {}, []
    for path in files:
        imported, packages = imports(path, internal)
        if path.name == "__init__.py" and not imported and not packages:
            continue
        name = module_name(path)
        text = label(
            name,
            summary(path),
            "imports " + ", ".join(sorted(packages)) if packages else "",
        )
        folders.setdefault(path.parent, []).append(
            f"    {node_id('module', name)}[{text}]"
        )
        edges += [
            f"  {node_id('module', name)} --> {node_id('module', target)}"
            for target in sorted(imported)
        ]
    lines = ["flowchart TB"]
    for folder, nodes in folders.items():
        package = folder / "__init__.py"
        title = (
            rel(folder) + "/" + (": " + summary(package) if package.exists() else "")
        )
        lines += [f"  subgraph {node_id('folder', rel(folder))}[{label(title)}]"]
        lines += nodes + ["  end"]
    return lines + edges


def render() -> str:
    workflows = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    }
    steps = load_steps(workflows)
    views = [
        ("Overview", OVERVIEW, overview(steps)),
        ("Pipeline", PIPELINE, pipeline(workflows, steps)),
        ("Modules", MODULES, modules()),
    ]
    parts = [INTRO]
    for title, text, diagram in views:
        body = "\n".join([FRONT_MATTER, *diagram])
        parts.append(f"## {title}\n\n{text}\n\n```mermaid\n{body}\n```\n")
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
