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
4. The pipeline refreshes the live sources every day, publishes the app and
   mirrors `data/events/` to a public Hugging Face dataset. The app does not
   read the mirror.

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
    "Each workflow in `.github/workflows/`: its triggers and settings, then one "
    "box per job, from left to right along the `needs` of the jobs. A box gives "
    "the job settings, then its steps in order. An arrow label names the job "
    "outputs that the next job reads. Dashed jobs and steps run only when their "
    "`if` condition holds. A step that uses secrets or variables names them: "
    "they are set in the repository settings (Secrets and variables, Actions)."
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


def uses(spec) -> str:
    """The variables and secrets that a workflow object uses, on one line."""
    names = [
        f"{kind}.{name}"
        for kind in ("vars", "secrets")
        for name in references(spec, kind)
    ]
    return "uses " + ", ".join(names) if names else ""


def load_steps(workflows: dict) -> list[dict]:
    """Every step of every job, with a node id unique in the page."""
    steps, seen = [], {}
    for file, workflow in workflows.items():
        for job, spec in workflow["jobs"].items():
            for step in spec.get("steps", []):
                title = step.get("name") or step.get("uses") or step["run"]
                # Jobs repeat steps such as the checkout: the job keeps them apart.
                key = node_id("step", job, title)
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


def step_outputs(text: str) -> list[tuple[str, str]]:
    """(step id, output name) of each `steps.<id>.outputs.<name>` in a text."""
    return re.findall(r"steps\.([\w-]+)\.outputs\.([\w-]+)", text)


def job_outputs(spec, job: str) -> list[str]:
    """Names of the outputs of a needed job that a workflow object reads."""
    pattern = rf"needs\.{re.escape(job)}\.outputs\.([\w-]+)"
    return sorted(set(re.findall(pattern, str(spec))))


def output_links(workflows: dict, steps: list[dict]) -> list[tuple[dict, str, dict]]:
    """(producer, output, reader) for each step that reads the output of a step.

    A step reads an output of an earlier step of its job, or an output of a job
    it needs, which the job takes from one of its own steps.
    """
    links = []
    for reader in steps:
        jobs = workflows[reader["file"]]["jobs"]
        sources = [
            (reader["job"], source, output)
            for source, output in step_outputs(str(reader["spec"]))
        ]
        for job in jobs:
            for output in job_outputs(reader["spec"], job):
                value = str(jobs[job].get("outputs", {}).get(output, ""))
                sources += [(job, source, output) for source, _ in step_outputs(value)]
        for job, source, output in sources:
            links += [
                (producer, output, reader)
                for producer in steps
                if producer["file"] == reader["file"]
                and producer["job"] == job
                and producer["spec"].get("id") == source
            ]
    return links


def output_edges(links: list[tuple[dict, str, dict]]) -> list[str]:
    """Dotted edges from a step to the steps that read its outputs."""
    edges = [
        f"  {producer['id']} -.->|{output}| {reader['id']}"
        for producer, output, reader in links
    ]
    return list(dict.fromkeys(edges))


def overview(workflows: dict, steps: list[dict]) -> list[str]:
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

    delivery = [step for step in steps if uses(step["spec"])]
    title = label("Workflow steps that use secrets or variables")
    lines.append(f"  subgraph delivery[{title}]")
    for step in delivery:
        text = label(f"{step['file']}: {step['title']}", uses(step["spec"]))
        lines.append(f"    {step['id']}([{text}])")
    lines.append("  end")
    for step in delivery:
        run = step["spec"].get("run", "")
        if re.search(r"docker (buildx )?(build|push)", run):
            lines.append(f"  image --> {step['id']}")
        if rel(DATA_DIR) in run:
            lines.append(f"  events --> {step['id']}")
    links = output_links(workflows, steps)
    return lines + output_edges(
        [link for link in links if link[0] in delivery and link[2] in delivery]
    )


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
        uses(spec),
        f"if: {spec['if']}" if "if" in spec else "",
    )
    return f"{step['id']}[{text}]" + (":::conditional" if "if" in spec else "")


def job_box(file: str, job: str, spec: dict, steps: list[dict]) -> list[str]:
    """A job as a box: its settings, then its steps in order."""
    box = node_id("job", file, job)
    # The jobs run left to right, the steps of a job top to bottom.
    lines = [
        f"    subgraph {box}[{label(spec.get('name', job))}]",
        "      direction TB",
    ]
    text = label(
        "job " + job,
        *(
            f"{key}: {describe(value)}"
            for key, value in spec.items()
            if key not in ("name", "needs", "if", "steps")
        ),
        f"if: {spec['if']}" if "if" in spec else "",
    )
    lines.append(f"      {box}_settings[{text}]")
    previous = f"{box}_settings"
    for step in steps:
        if step["file"] == file and step["job"] == job:
            lines.append(f"      {step_node(step)}")
            lines.append(f"      {previous} --> {step['id']}")
            previous = step["id"]
    lines.append("    end")
    return lines


def pipeline(workflows: dict, steps: list[dict]) -> list[str]:
    lines = ["flowchart LR", "  classDef conditional stroke-dasharray: 6 4"]
    conditional = []
    for file, workflow in workflows.items():
        flow = node_id("workflow", file)
        lines.append(f"  subgraph {flow}_box[{label('.github/workflows/' + file)}]")
        lines.append("    direction LR")
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
        # After every box: a needs edge must not create a job before its box.
        edges = []
        for job, spec in workflow["jobs"].items():
            box = node_id("job", file, job)
            lines += job_box(file, job, spec, steps)
            if "if" in spec:
                # Mermaid styles a box only from a class line outside every box.
                conditional.append(f"  class {box} conditional")
            needs = spec.get("needs", [])
            for needed in [needs] if isinstance(needs, str) else needs:
                outputs = ", ".join(job_outputs(spec, needed))
                arrow = f"-->|{outputs}|" if outputs else "-->"
                edges.append(f"    {node_id('job', file, needed)} {arrow} {box}")
            if not needs:
                edges.append(f"    {flow} --> {box}")
        lines += edges
        lines.append("  end")
    # Mermaid keeps the top to bottom direction of a job box only while its
    # edges stay inside it or go to the boxes and nodes beside it. Hence output
    # edges inside a job only (the needs edges name the job outputs), and step
    # labels that name the secrets and variables instead of edges to them.
    links = output_links(workflows, steps)
    return (
        lines
        + conditional
        + output_edges([link for link in links if link[0]["job"] == link[2]["job"]])
    )


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
        ("Overview", OVERVIEW, overview(workflows, steps)),
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
