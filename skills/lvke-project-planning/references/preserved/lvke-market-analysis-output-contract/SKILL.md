---
name: lvke-market-analysis-output-contract
description: Enforce structured market analysis output covering industry form, market environment, industry and supply chains, scale, supply-demand, competition, saturation, market share, pricing, competitiveness, and marketing strategy with traceable sources.
---

# Market Analysis Output Contract

Market analysis for formal feasibility must produce structured, sourced answers to every dimension below. Do not proceed to build scale, revenue drivers, or finance until all evidence-required fields have traceable EvidencePack support. Analytical recommendations must identify the evidence and reasoning used.

## Required Structured Outputs

| Dimension | Required Fields | Evidence Requirement |
|---|---|---|
| **Industry Form** | Industry category, business form, product/service model, lifecycle stage | Industry classification or authoritative industry description |
| **Target Market Environment** | Target customers, geography, policy/economic context, addressable capacity | Regional facts with explicit period and scope |
| **Industry and Supply Chains** | Upstream inputs, key suppliers, downstream customers/channels, bottlenecks and dependencies | Named chain participants or authoritative chain analysis |
| **Industry Scale** | Total market size (value/volume), historical growth rate, forecast period | ≥2 independent sources with locator |
| **Regional Supply-Demand** | Regional capacity, demand, supply-demand gap, utilization rate | Regional data or explicit regional adjustment from national |
| **Market Saturation** | Current saturation level, entry barriers, market maturity stage | Comparative analysis or expert assessment with citation |
| **Competition Analysis** | Key competitors, market concentration, competitive positioning | Named competitors with market share or capacity data |
| **Product/Service Competitiveness** | Differentiators, substitutes, cost/quality/service comparison, defensibility | Comparable products/services and explicit comparison basis |
| **Target Market Share** | Achievable share, share justification, target volume/revenue | Derived from MarketSizingCase with explicit selection reason |
| **Pricing Analysis** | Market price range, pricing trend, price elasticity (if applicable) | Historical price series or transaction data with period |
| **Marketing Strategy** | Target segments, positioning, channels, acquisition approach, phased actions and measurable targets | Derived recommendation tied to market, competition, pricing, and capacity findings |

## Workflow

1. **Intake**: Read the confirmed `ResearchPackage` and `EvidencePack`.
2. **Verify Coverage**: Check that analysis_extract_candidates or research findings cover all eleven dimensions above.
3. **Flag Gaps**: If any dimension lacks formal evidence, mark it as `missing_inputs` and block progression to MarketSizingCase confirmation.
4. **Structured Output**: Present all eleven dimensions as a structured summary with locators and clearly label derived recommendations before calling `planning_confirm(object_kind="market_case")`.

## Integration with Existing Tools

- Use `lvke-market-sizing` for the core MarketSizingCase workflow.
- This contract adds a **pre-confirmation checklist** that ensures all eleven dimensions are documented.
- If the research package is `partial` or `source_reconstructed`, state limitations explicitly and do not claim formal delivery.

## Anti-patterns

- Skipping competition/saturation because "market is large enough"
- Omitting industry/supply-chain dependencies or substituting generic industry prose
- Giving a marketing strategy that is not derived from the selected target market, price, and competitive position
- Using search summaries as evidence for competitive positioning
- Averaging conflicting price data without explicit selection reasoning
- Proceeding to finance model with "TBD" in any required dimension
