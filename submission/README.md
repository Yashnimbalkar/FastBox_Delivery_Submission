# FastBox Mystery Delivery System

A one-day logistics simulator for the fictional delivery company **FastBox**,
built for the "Python Assignment: Mystery Delivery System" brief.

## Files

- `delivery_system.py` — the full solution (single, well-commented script).
- `data.json` — the sample input from the assignment PDF.
- `report.json` — the report generated from `data.json`.

## How it works

1. **Parse** — `data.json` is read with the standard `json` module, then
   walked field-by-field into a simple internal shape (no pandas/shortcuts).
   The loader accepts **both** JSON shapes seen across the provided files:
   - the PDF/`test_case_*.json` shape (`{"warehouses": {"W1": [x, y]}}`), and
   - the `base_case.json` shape (`{"warehouses": [{"id": "W1", "location": [x, y]}]}`).
2. **Assign** — every package is matched to whichever agent starts nearest
   (Euclidean distance) to that package's *warehouse*.
3. **Simulate** — each agent works through their assigned packages in order.
   For each one: travel from the current position to the warehouse, pick up,
   then travel to the destination. The agent's position updates after every
   delivery, so the next trip starts from wherever they just finished.
4. **Report** — for each agent: `packages_delivered`, `total_distance`
   (rounded to 2 dp), and `efficiency` (`total_distance / packages_delivered`
   — lower is better). `best_agent` is whoever has the lowest efficiency
   among agents who delivered at least one package.
5. **Save** — the report is written to `report.json` (or wherever `-o` points).

A built-in sanity check asserts that packages delivered across all agents
always equals the number of packages in the input, so a bug in the
assignment logic would fail loudly rather than silently under/over-counting.

## Assumptions (the brief leaves these open)

- Nearest-agent assignment uses each agent's **starting** location — it
  doesn't get recomputed as agents move during the day.
- Package delivery order per agent follows the order in the input file.
  (There's no requirement to solve for an optimal route — the brief only
  asks for total distance travelled.)
- "Efficiency" = distance per package delivered (lower is better); this
  matches the example in the brief, where the agent with the *lowest*
  efficiency (`A1`, 42.66) was named `best_agent`.
- The example numbers in the assignment PDF illustrate the report's
  *shape*, not exact expected values — plugging the PDF's own sample data
  into different (equally reasonable) distance-accumulation strategies
  doesn't reproduce those numbers exactly, so they're clearly illustrative.

## Usage

```bash
# Basic run
python delivery_system.py data.json

# Custom output path
python delivery_system.py data.json -o my_report.json
```

## Bonus features

All optional, off by default:

```bash
# ASCII-visualize warehouses (W), agent start points (A), and delivery stops (.)
python delivery_system.py data.json --ascii

# Simulate random delivery delays (adds "delay_minutes" per agent to the report)
python delivery_system.py data.json --delays --seed 42

# A new agent joins partway through the day (e.g. agent A5 starts at (12, 34)
# and takes over any package assignment from the 7th package onward)
python delivery_system.py data.json --join-agent "A5:12,34@6"

# Export the top-performing agent's stats to a CSV
python delivery_system.py data.json --top-performer-csv top_agent.csv
```

## Tested against

The script was run successfully against every file that shipped with the
assignment (`base_case.json` and all 10 `test_case_*.json` files) plus the
sample `data.json` from the PDF itself. In every case the sanity check
confirmed all input packages were delivered exactly once.
