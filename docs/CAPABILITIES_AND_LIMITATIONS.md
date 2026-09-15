# Capabilities and limitations

## Supported responsibility

The package performs deterministic source-structure operations on a local WRF
checkout. It reports what was extracted and where it came from. A person remains
responsible for scientific interpretation and implementation decisions.

## Proven capabilities

| Capability | Output | Important boundary |
|---|---|---|
| Source identity | Manifest JSON | Describes the checkout, not runtime behavior |
| Fortran extraction | Index JSON | Structural subset, not full compiler semantics |
| Procedure lookup | Fortran index JSON | Names may be duplicated or configuration-dependent |
| Interface extraction | Arguments, declarations, INTENT | Some preprocessor alternatives remain conditional |
| Direct callers/callees | Index and graph records | Indirect and type-bound calls may be unresolved |
| Argument mapping | Actual-to-dummy mappings | Mapping does not itself prove mutation |
| Scheme graph | JSON and SVG | Requires a defensible root and configuration |
| PBL Registry inventory | Inventory JSON | Targeted to known WRF file categories |
| Assignment extraction | Expression evidence | Does not validate equations scientifically |
| Reference integration comparison | JSON and Markdown | Limited to declarations in the packaged WRF layout |

## Explicitly unsupported

- Scientific-paper comparison or equation validation.
- Automatic code changes, compilation, or runtime validation.
- Guarantees of complete call graphs or dataflow.
- Software families other than WRF.

## Correct use

Use the tool to replace manual source searching and graph construction with
repeatable evidence. Inspect unresolved edges and configuration assumptions.
Treat scientific conclusions as a separate review task.
