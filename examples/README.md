# Examples

## Supported starting points

- `standalone/run_sample_demo.sh` runs entirely on the included miniature
  WRF-like source fixture. Start here.
- `standalone/run_real_mynn.sh /path/to/WRF` indexes a real WRF checkout and
  produces a MYNN direct-call graph.
- `standalone/run_scheme_graph.sh` is a reusable wrapper accepting a WRF path,
  scheme root, entry, variable, and configuration values.
- `standalone/run_surface_revised_mm5.sh` demonstrates a surface-layer graph.
- `standalone/run_cumulus_kf.sh` demonstrates a cumulus graph.

Both examples run only deterministic source-analysis operations.

## Advanced regression scripts

The remaining scripts exercise narrower capabilities against the locally
tested WRF 4.7.1 checkout: SMS-3DTKE assignment extraction and the surface-layer
implementation comparator. Their numeric assertions are version-specific test
oracles, not universal scientific facts.
