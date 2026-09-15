# WRF deterministic source tools

This is an offline Python package for inspecting Weather Research and
Forecasting (WRF) source code through explicit, reproducible operations.

## Requirements

- Python 3.10 or newer
- A local WRF source tree for full analysis
- No third-party Python runtime packages
- No root access

Install into a local virtual environment:

```bash
cd wrf-doc-assistant
./install_local.sh
. .venv/bin/activate
wrf-source --help
```

## Self-contained demonstration

The included miniature Fortran fixture requires no WRF download:

```bash
./examples/standalone/run_sample_demo.sh
```

It creates a Fortran index plus JSON and SVG direct-call graphs under
`generated/sample-demo/`.

## Real WRF example

```bash
./examples/standalone/run_real_mynn.sh /path/to/WRF
```

This indexes the supplied checkout and creates a configuration-filtered MYNN
direct-call graph under `generated/real-mynn/`.

## Other WRF physics schemes

The extractor can index tracked Fortran for PBL, surface layer, cumulus,
microphysics, radiation, land-surface, and other WRF components. A useful
scheme-isolated graph still requires the correct driver procedure, driver file,
entry call, and selector values for that WRF version.

Tested examples:

```bash
./examples/standalone/run_surface_revised_mm5.sh /path/to/WRF
./examples/standalone/run_cumulus_kf.sh /path/to/WRF
```

Use `run_scheme_graph.sh` for another component after identifying those
integration values. The package does not infer them from a scheme nickname.

## Individual operations

Record source identity:

```bash
wrf-source manifest --wrf /path/to/WRF --output generated/wrf-manifest.json
```

Extract the complete recognized Fortran tree:

```bash
wrf-source extract --wrf /path/to/WRF --output generated/fortran-index.json
```

Generate a selected direct-call graph:

```bash
wrf-source graph \
  --index generated/fortran-index.json \
  --scheme MYNN \
  --root pbl_driver \
  --root-path phys/module_pbl_driver.F \
  --set bl_pbl_physics=5 \
  --set mynnpblscheme=5 \
  --variable qke \
  --max-depth 8 \
  --output-prefix generated/mynn
```

Extract assignments for chosen variables and procedures:

```bash
wrf-source equations \
  --index generated/fortran-index.json \
  --graph generated/mynn.json \
  --source-root /path/to/WRF \
  --variable qke \
  --procedure mynnedmf_driver \
  --output-prefix generated/mynn-qke
```

Compare WRF integration points for declared reference schemes:

```bash
wrf-source implementation-template \
  --index generated/fortran-index.json \
  --source-root /path/to/WRF \
  --component surface-layer \
  --reference MYNN \
  --reference revised-MM5 \
  --output-prefix generated/surface-comparison
```

## Supported outputs

- Source and build manifest JSON
- PBL-focused WRF inventory JSON
- Fortran modules, procedures, ordered arguments, declarations, `INTENT`,
  imports, calls, assignments, loops, conditions, and source spans
- Configuration-filtered direct-call graphs in JSON and SVG
- Actual-to-dummy argument mappings
- Scoped assignment reports in JSON and Markdown
- WRF reference-scheme integration comparisons in JSON and Markdown

See [CAPABILITIES_AND_LIMITATIONS.md](docs/CAPABILITIES_AND_LIMITATIONS.md) for
the exact analysis boundary, [USER_GUIDE.md](docs/USER_GUIDE.md) for a plain
language walkthrough, and [examples/README.md](examples/README.md) for the
example layout.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

All normal tests are offline and deterministic.

## License

Released under the [MIT License](LICENSE).
