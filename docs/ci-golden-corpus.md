# Lvke Golden Corpus

P0A freezes the byte identity of 46 source documents. P0B is a separate
business approval state. A successful P0A verification never means that P0B
has been approved.

The default corpus is this repository. CI may mount the same relative tree at
another location with `LVKE_GOLDEN_DATA_ROOT` or `--data-root`.

```bash
conda run -n lvke-mcp python scripts/golden_samples_manifest.py --verify
```

The frozen groups are:

- `huangyingyan`: 15 source files under `docs/项目流程`
- `finance_templates`: 10 original Word/Excel templates
- `hengli_hotel`: 20 preliminary source files plus one reference report

Derived formula Markdown, review notes, extraction scripts, and generated
deliverables are intentionally excluded from P0A.

P0B starts as `pending_business_approval` with `expected_results=[]` and
`last_passing_build=null`. Only real reference-track and corrected-track
approval records may be frozen:

```bash
conda run -n lvke-mcp python scripts/golden_samples_manifest.py \
  --freeze-p0b /absolute/path/APPROVED.json
```

A passing build can be recorded only after P0B is frozen, all three groups ran,
the record targets the current Git HEAD, and skipped, timed-out, and temporary
dependency fields are empty:

```bash
conda run -n lvke-mcp python scripts/golden_samples_manifest.py \
  --record-build /absolute/path/PASSED_BUILD.json
```

The tool rejects absolute paths, `..` traversal, any symlink component, file
size or SHA-256 changes, incomplete group coverage, incomplete approval fields,
and build records containing skipped work.
