# Human-readable user guide

## What this package is

`wrf-source` reads WRF source files and turns selected source structure into
machine-readable indexes, compact reports, and call graphs. It is useful when
manually following a large Fortran code path would be slow and error-prone.

For a Git checkout, whole-tree extraction uses Git-tracked files and recursive
submodule files. This avoids indexing generated build copies alongside their
original sources. A non-Git source directory falls back to scanning its files.

It never modifies the WRF checkout.

## A simple example

Run:

```bash
./examples/standalone/run_sample_demo.sh
```

The script reads two small example Fortran files and produces:

- `fortran-index.json`: procedures, arguments, declarations, calls, and spans;
- `demo-pbl.json`: the selected call graph as structured data;
- `demo-pbl.svg`: the same graph as a picture viewable in a browser.

The graph arrow `pbl_driver -> demo_pbl` means the first procedure directly
calls the second in the extracted source. It does not mean the second procedure
modifies the first procedure.

## Working with real WRF

```bash
./examples/standalone/run_real_mynn.sh /path/to/WRF
```

This performs two potentially time-consuming steps:

1. Index every recognized Fortran source file in the supplied WRF checkout.
2. Starting at `pbl_driver`, retain direct calls matching the supplied MYNN
   configuration and render the resulting JSON/SVG graph.

It does not run WRF or prove which branch was taken in a particular simulation.
The selected configuration values are assumptions supplied to the graph tool.

## Reusable scheme-graph example

Use the generic wrapper when you already know the WRF driver, scheme entry
procedure, and relevant selector values:

```bash
./examples/standalone/run_scheme_graph.sh \
  /path/to/WRF \
  mynn-example \
  MYNN \
  pbl_driver \
  phys/module_pbl_driver.F \
  mynnedmf_driver \
  qke \
  bl_pbl_physics=5 \
  mynnpblscheme=5
```

Replace the scheme, root, source path, entry procedure, variable, and settings
for another WRF component. Use `-` for the entry or variable when no filter is
desired.

## Surface-layer example

The Revised MM5 surface-layer path in WRF 4.7.1 starts in `surface_driver`, is
selected by `sf_sfclay_physics=1`, and enters `sfclayrev`:

```bash
./examples/standalone/run_surface_revised_mm5.sh /path/to/WRF
```

Equivalent generic command:

```bash
./examples/standalone/run_scheme_graph.sh \
  /path/to/WRF surface-revised-mm5 REVISED_MM5 \
  surface_driver phys/module_surface_driver.F sfclayrev - \
  sf_sfclay_physics=1 sfclayrevscheme=1
```

On the tested WRF 4.7.1 index this produced 4 procedure nodes and 3 direct-call
edges.

## Cumulus example

The Kain-Fritsch Eta path in WRF 4.7.1 starts in `cumulus_driver`, is selected
by `cu_physics=1`, and enters `kf_eta_cps`:

```bash
./examples/standalone/run_cumulus_kf.sh /path/to/WRF
```

Equivalent generic command:

```bash
./examples/standalone/run_scheme_graph.sh \
  /path/to/WRF cumulus-kain-fritsch KAIN_FRITSCH \
  cumulus_driver phys/module_cumulus_driver.F kf_eta_cps - \
  cu_physics=1 kfetascheme=1
```

On a clean Git-tracked WRF 4.7.1 index this produced 9 procedure nodes and 16
direct-call edges.

These are source-version-specific configurations, not universal aliases. For a
different WRF release, verify the selector constant, driver branch, entry name,
and source path before relying on the graph.

## Practical operations

### Record exactly which checkout was inspected

```bash
wrf-source manifest --wrf /path/to/WRF --output generated/manifest.json
```

### Create a complete Fortran structure index

```bash
wrf-source extract --wrf /path/to/WRF --output generated/index.json
```

### Produce a visual direct-call graph

Use `wrf-source graph` or the generic wrapper above. The JSON is intended for
programmatic inspection; the SVG is intended for people.

### Extract selected assignment expressions

```bash
wrf-source equations \
  --index generated/index.json \
  --graph generated/mynn.json \
  --source-root /path/to/WRF \
  --variable qke \
  --procedure mynnedmf_driver \
  --output-prefix generated/mynn-qke
```

### Compare known WRF scheme integration points

The implementation comparator uses the included WRF layout definition to
compare declared reference schemes. It can report driver calls, selector
values, Registry locations, interface differences, initialization hooks, and
compatibility checks. It is a comparison aid, not an automatic implementation.

## What it can do

- Reproduce a source inventory for a specific WRF checkout.
- Extract modules, procedures, ordered arguments, declarations, `INTENT`,
  imports, direct calls, conditions, loops, and assignments.
- Resolve many direct calls using lexical module and `USE` information.
- Map actual call arguments to dummy procedure arguments.
- Isolate a bounded graph using supplied roots, entries, variables, and WRF
  configuration values.
- Compare selected, declared WRF reference-scheme integration points.
- Preserve source locations and hashes for auditability.

## What it cannot do

- Execute WRF or observe an actual runtime call order.
- Guarantee a complete graph for procedure pointers, generated code,
  type-bound calls, or every preprocessor configuration.
- Prove that a variable was modified merely because it appears in a call.
- Explain the physical meaning or scientific correctness of an equation.
- Read a paper and compare its mathematics with WRF automatically.
- Discover every selector and entry point for an arbitrary scheme without the
  user supplying a defensible starting configuration.
- Automatically infer the correct scheme settings merely from a friendly
  scheme name.
- Write, compile, or scientifically validate a new WRF scheme.
- Replace a Fortran compiler, debugger, profiler, or domain expert.

## Reading results safely

Treat the index and graphs as extracted source structure. Check unresolved
calls, retained preprocessor conditions, selected configuration values, and
source spans before making an implementation decision.
