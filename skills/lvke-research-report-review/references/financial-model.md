# Financial Model Domain

Use `FINMODEL.ASSUMPTION.LOGIC`. Start from deterministic workbook scan/recalculation results.

Check formula dependencies, copied-formula breaks, hardcoded calculations, external links, hidden sheets, defined names, circular/iterative settings, stale or missing caches, units, signs, periods, tax, debt, depreciation, working capital, terminal assumptions, sensitivity, and scenario linkage.

XLSM macros are static evidence only and are never executed. Encrypted, external-dependent, macro-dependent, or unrecalculable workbooks must state the limitation and cannot receive a reproducibility pass. For internal models, require deterministic replay from the frozen Spec/BoE/Run lineage.

