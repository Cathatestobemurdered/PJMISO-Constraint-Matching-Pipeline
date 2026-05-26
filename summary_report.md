# Constraint Mapping Across Market, Dayzer, and Panorama
### Summary Report — Yingyun Zhan

## What the map tells a power trader

The governing conclusion is this: the map is most reliable exactly where it matters least for trading, and least reliable exactly where it matters most. Coverage looks excellent in aggregate, but the constraints that drive forward congestion value are concentrated in the part of the map that needs the most care. Four findings support that, and each carries a direct implication for how to trade on this data.

### The tradeable congestion is the hard-to-match congestion

Congestion that can be monetized in futures and FTRs comes overwhelmingly from outage contingencies, not from the base case. Base-case constraints reflect the system as normally configured; they are 24% of the Market list and they match cleanly, at 78% high confidence. The remaining 76% are defined around a specific outage, and those match at only 60% high confidence. The matching is therefore weakest on precisely the constraints whose binding generates the price separations a desk wants to capture. A trader who screens the map by confidence alone will end up trusting the quiet, low-value constraints and discarding the volatile, high-value ones. The right read is the opposite: treat a medium-confidence outage match as worth a manual look, because that is where the basis lives.

### The biggest price-moving events are the ones the pipeline currently mis-routes

Loss-of-element contingencies are 6% of the list but 62% of everything flagged for review, and within that group 238 constraints sit on the 345 kV, 500 kV, and 765 kV backbone. These are the outages that move LMP separations the most, and they are the ones the current voltage parser handles worst, scoring only 6% high confidence. The cause is mechanical: a constraint like "345 kV element, loss of a 765 kV line" carries two voltages, and the parser reads the 765 from the outage clause instead of the 345 of the monitored element, routing it to the wrong neighborhood. For a desk this is the highest-value repair in the whole pipeline, because fixing one parsing rule recovers the cross-source identity of the backbone events that dominate congestion P&L. Until it is fixed, backbone loss-of constraints should be reconciled by hand rather than trusted from the score.

### Forward simulation and settlement do not share a granularity, so shadow prices will not map one-to-one

Dayzer aggregates contingencies that Market enumerates separately. A single Dayzer record such as "APSOUTH : L500.Bedington-Black Oak" is the best match for eight distinct Market constraints, and across the file 35% of all Market matches land on a Dayzer record that is shared by more than one Market row. The consequence for trading is concrete: a shadow price or binding frequency that Dayzer reports against one monitored element corresponds to a bundle of Market settlement constraints, not a single one. Anyone valuing an FTR by lining up a Dayzer forward signal against a Market constraint one-for-one will misattribute the congestion. The matched IDs are exposed in the output so this collapsing is visible, but the modelling fix is to treat Dayzer as the element-level layer and fan its signal out across the Market contingencies it covers.

### Panorama is a backtest archive, and most of it is noise

Panorama matches the Market facility set slightly better than Dayzer, at 99.2% versus 95.7%, because it restates the facility in close to Market's own wording. But 87% of those matches point at history, and the archive is dominated by transient events: 59% of Panorama constraints were active for under a month, while only 12% persisted beyond three years. For backtesting that is the useful cut. The recurring, structural constraints, the ones worth building a seasonal congestion view around, are that long-lived 12%; the short-lived majority is one-off noise that will not inform a forward position. The staleness flag and the last-seen date in the output are what let a desk filter to the structural set rather than fit to ephemera.

## What to do with it

Read the map as a high-recall candidate set, then apply trading judgment in the order the findings imply. Trust the base-case matches and move on. Pull the outage matches, especially anything on the 345 kV and above backbone, for a manual pass, because that is where both the value and the current error are concentrated. When connecting Dayzer's forward view to Market settlement, fan one Dayzer element out to its Market contingencies rather than assuming a single counterpart. And when mining Panorama for seasonality, filter to the long-lived structural constraints and discard the transient majority.

The single highest-leverage improvement is the loss-of voltage parsing, because it sits directly on the backbone events that drive congestion P&L. Everything else, a two-sided reconciliation to confirm the crosswalk, a stronger contingency signal on base-case rows, is incremental by comparison.

## Output and reproduction

The CSV leads with the three required columns, `market_constraint`, `dayzer_constraint`, and `pano_constraint`, followed by per-source score, confidence, status, and ID fields, the Panorama last-seen date and staleness flag, and an overall confidence taken as the weaker of the two matches so a three-way link is never over-claimed. Status is labeled matched, review, or unmatched, and the proposed name is always retained even on weak matches so a reviewer can judge what was suggested.

Running `python pipeline.py` regenerates the CSV in about a minute. The matcher uses rapidfuzz when installed and otherwise falls back to a pure-Python scorer that reproduces the same scores, so results are identical in either environment, and the notebook imports the same module so the script and the notebook cannot drift apart.
