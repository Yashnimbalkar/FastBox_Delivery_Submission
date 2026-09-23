"""
FastBox Mystery Delivery System
================================

A one-day logistics simulation for a fictional delivery company, FastBox.

Given a set of warehouses, delivery agents, and packages, this program:
  1. Parses the input JSON "by hand" (plain `json` module + manual walking
     of the resulting dict/list structures -- no pandas/numpy shortcuts).
  2. Assigns every package to the agent nearest (Euclidean distance) to the
     warehouse the package ships from.
  3. Simulates each agent's day: starting from their own location, an agent
     travels to a warehouse, picks up the package, and carries it to the
     destination. Their position updates after every delivery, so the next
     trip starts from wherever they just finished, not from home base.
  4. Produces a report of packages delivered, distance travelled, and an
     "efficiency" score (distance per package -- lower is better) for every
     agent, plus the id of the single most efficient agent.
  5. Saves that report to report.json.

Design assumptions (the brief leaves these open, so they're documented here
and easy to change):
  - "Nearest agent" is based on each agent's STARTING location vs. the
    warehouse -- assignment happens once, up front, before any simulated
    movement (this matches the brief: "nearest agent based on Euclidean
    distance from agent to warehouse").
  - An agent that has been assigned N packages delivers them in the order
    they appear in the input file, one at a time: current position ->
    warehouse -> destination -> (position updates) -> next warehouse -> ...
    This is the simplest faithful reading of "agent picks up packages from
    warehouse and delivers to destination. Compute total distance travelled."
  - "efficiency" = total_distance / packages_delivered (lower = better,
    i.e. fewer km per parcel). The most efficient agent is whoever has the
    lowest efficiency score among agents who delivered at least one package.
  - All distances are plain 2D Euclidean distance, rounded to 2 decimals
    only at the point of reporting (accumulated internally at full
    precision to avoid compounding rounding error).

Supported input formats
------------------------
Two JSON shapes are accepted so the same program works against every file
that shipped with the assignment:

  A) The "spec" shape from the assignment PDF / test_case_*.json:
       {
         "warehouses": {"W1": [x, y], ...},
         "agents":     {"A1": [x, y], ...},
         "packages":   [{"id": "P1", "warehouse": "W1", "destination": [x, y]}, ...]
       }

  B) The "list" shape used by base_case.json:
       {
         "warehouses": [{"id": "W1", "location": [x, y]}, ...],
         "agents":     [{"id": "A1", "location": [x, y]}, ...],
         "packages":   [{"id": "P1", "warehouse_id": "W1", "destination": [x, y]}, ...]
       }

Usage
-----
    python delivery_system.py data.json [-o report.json] [--ascii] [--delays]
                                          [--join-agent "A5:12,34@30"]
                                          [--top-performer-csv top_agent.csv]
"""

import argparse
import csv
import json
import math
import random
import sys


# ---------------------------------------------------------------------------
# 1. Manual JSON parsing / normalisation
# ---------------------------------------------------------------------------

def load_raw_json(path):
    """Read the file and parse it with the standard library json module.

    ("Manual" parsing here means: we don't hand the file to pandas or any
    other data-loading helper that would parse *and* reshape the data for
    us. We load plain Python dicts/lists and walk them ourselves below.)
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_data(raw):
    """Convert either supported input shape into a single internal shape:

        warehouses: {warehouse_id: (x, y)}
        agents:     {agent_id: (x, y)}
        packages:   [{"id": pkg_id, "warehouse": warehouse_id, "destination": (x, y)}]

    Raises ValueError with a clear message if the structure isn't recognised.
    """
    warehouses = _normalize_points(raw.get("warehouses"), location_key="location")
    agents = _normalize_points(raw.get("agents"), location_key="location")
    packages = _normalize_packages(raw.get("packages"))

    if not warehouses:
        raise ValueError("No warehouses found in input data.")
    if not agents:
        raise ValueError("No agents found in input data.")

    return warehouses, agents, packages


def _normalize_points(entries, location_key):
    """Handle both {"W1": [x, y]} and [{"id": "W1", "location": [x, y]}]."""
    points = {}

    if entries is None:
        return points

    if isinstance(entries, dict):
        # Shape A: {"W1": [x, y], "W2": [x, y], ...}
        for entity_id, coords in entries.items():
            points[entity_id] = (float(coords[0]), float(coords[1]))

    elif isinstance(entries, list):
        # Shape B: [{"id": "W1", "location": [x, y]}, ...]
        for entry in entries:
            entity_id = entry["id"]
            coords = entry[location_key]
            points[entity_id] = (float(coords[0]), float(coords[1]))

    else:
        raise ValueError(f"Unrecognised warehouses/agents structure: {type(entries)}")

    return points


def _normalize_packages(entries):
    """Handle both {"warehouse": "W1", ...} and {"warehouse_id": "W1", ...}."""
    packages = []

    if entries is None:
        return packages

    for entry in entries:
        warehouse_id = entry.get("warehouse", entry.get("warehouse_id"))
        dest = entry["destination"]
        packages.append({
            "id": entry["id"],
            "warehouse": warehouse_id,
            "destination": (float(dest[0]), float(dest[1])),
        })

    return packages


# ---------------------------------------------------------------------------
# 2. Distance helper
# ---------------------------------------------------------------------------

def euclidean_distance(p1, p2):
    """Straight-line distance between two (x, y) points."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


# ---------------------------------------------------------------------------
# 3. Package -> agent assignment
# ---------------------------------------------------------------------------

def assign_packages_to_agents(packages, warehouses, agents):
    """Assign every package to the agent nearest to that package's warehouse.

    Returns: {agent_id: [package, package, ...]}  (insertion order preserved)
    """
    assignments = {agent_id: [] for agent_id in agents}

    for package in packages:
        warehouse_id = package["warehouse"]
        if warehouse_id not in warehouses:
            raise ValueError(
                f"Package {package['id']} references unknown warehouse '{warehouse_id}'."
            )
        warehouse_pos = warehouses[warehouse_id]

        nearest_agent = min(
            agents,
            key=lambda agent_id: euclidean_distance(agents[agent_id], warehouse_pos),
        )
        assignments[nearest_agent].append(package)

    return assignments


# ---------------------------------------------------------------------------
# 4. Simulation
# ---------------------------------------------------------------------------

def simulate_day(assignments, warehouses, agents, delay_config=None, rng=None):
    """Run each agent through their assigned packages in order.

    For every package: current position -> warehouse -> destination.
    The agent's position is updated to the destination after each delivery,
    so subsequent trips start from there (not from the agent's home base).

    If delay_config is given (a dict with "chance" and "max_minutes"), each
    delivery has a random chance of a delay; delay minutes are tracked per
    agent but never affect the distance calculation (delays are a time
    concept, not a distance one).

    Returns: {agent_id: {"packages_delivered": int,
                          "total_distance": float,   # full precision
                          "route": [pos, pos, ...],  # for ASCII visualisation
                          "delay_minutes": float}}
    """
    rng = rng or random.Random()
    results = {}

    for agent_id, agent_packages in assignments.items():
        current_pos = agents[agent_id]
        total_distance = 0.0
        total_delay = 0.0
        route = [current_pos]  # start point, for visualisation

        for package in agent_packages:
            warehouse_pos = warehouses[package["warehouse"]]
            destination_pos = package["destination"]

            total_distance += euclidean_distance(current_pos, warehouse_pos)
            total_distance += euclidean_distance(warehouse_pos, destination_pos)

            route.append(warehouse_pos)
            route.append(destination_pos)

            current_pos = destination_pos

            if delay_config and rng.random() < delay_config.get("chance", 0):
                total_delay += rng.uniform(0, delay_config.get("max_minutes", 15))

        results[agent_id] = {
            "packages_delivered": len(agent_packages),
            "total_distance": total_distance,
            "route": route,
            "delay_minutes": total_delay,
        }

    return results


# ---------------------------------------------------------------------------
# 5. Report generation
# ---------------------------------------------------------------------------

def build_report(sim_results, include_delays=False):
    """Turn raw simulation numbers into the rounded, human-facing report."""
    report = {}
    best_agent = None
    best_efficiency = math.inf

    for agent_id, data in sim_results.items():
        delivered = data["packages_delivered"]
        distance = data["total_distance"]
        efficiency = (distance / delivered) if delivered else 0.0

        agent_report = {
            "packages_delivered": delivered,
            "total_distance": round(distance, 2),
            "efficiency": round(efficiency, 2),
        }
        if include_delays:
            agent_report["delay_minutes"] = round(data["delay_minutes"], 2)

        report[agent_id] = agent_report

        # Only agents who actually delivered something can be "most efficient".
        if delivered and efficiency < best_efficiency:
            best_efficiency = efficiency
            best_agent = agent_id

    report["best_agent"] = best_agent
    return report


def sanity_check(report, total_packages):
    """Make sure every package was accounted for exactly once."""
    delivered_total = sum(
        v["packages_delivered"] for k, v in report.items() if k != "best_agent"
    )
    if delivered_total != total_packages:
        raise AssertionError(
            f"Package count mismatch: {delivered_total} delivered vs "
            f"{total_packages} in input."
        )


# ---------------------------------------------------------------------------
# 6. Bonus features
# ---------------------------------------------------------------------------

def render_ascii_map(warehouses, agents, sim_results, width=60, height=25):
    """Very small ASCII scatter-plot of warehouses (W), agent starts (A),
    and delivery stops (.) so routes can be eyeballed in a terminal.
    """
    all_points = list(warehouses.values()) + list(agents.values())
    for data in sim_results.values():
        all_points.extend(data["route"])

    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = (max_x - min_x) or 1
    span_y = (max_y - min_y) or 1

    grid = [[" " for _ in range(width)] for _ in range(height)]

    def plot(pos, symbol):
        col = int((pos[0] - min_x) / span_x * (width - 1))
        row = int((pos[1] - min_y) / span_y * (height - 1))
        row = height - 1 - row  # flip so +y is "up"
        grid[row][col] = symbol

    for w_id, pos in warehouses.items():
        plot(pos, "W")
    for a_id, pos in agents.items():
        plot(pos, "A")
    for data in sim_results.values():
        for pos in data["route"][1:]:  # skip the agent's own start point
            if grid[height - 1 - int((pos[1] - min_y) / span_y * (height - 1))][
                int((pos[0] - min_x) / span_x * (width - 1))
            ] == " ":
                plot(pos, ".")

    lines = ["".join(row) for row in grid]
    legend = "Legend: W = warehouse, A = agent start, . = delivery stop"
    return "\n".join(lines) + "\n" + legend


def add_agent_mid_day(agents, warehouses, packages, assignments, sim_results,
                       new_agent_id, new_agent_location, joined_after_package_index):
    """Demonstrate a new agent joining partway through the day.

    Packages up to `joined_after_package_index` (exclusive) have already
    been assigned/simulated with the original agent roster. Everything from
    that index onward is re-assigned across the ORIGINAL agents plus the
    new one, then re-simulated for just that remaining slice.

    Returns updated (assignments, sim_results) reflecting the new agent.
    """
    already_done = packages[:joined_after_package_index]
    remaining = packages[joined_after_package_index:]

    updated_agents = dict(agents)
    updated_agents[new_agent_id] = new_agent_location

    # Re-assign only the remaining packages across the full (updated) roster.
    remaining_assignments = assign_packages_to_agents(remaining, warehouses, updated_agents)

    # Keep the already-completed assignments for the original agents untouched.
    combined_assignments = {agent_id: list(pkgs) for agent_id, pkgs in assignments.items()}
    combined_assignments.setdefault(new_agent_id, [])
    for agent_id, pkgs in remaining_assignments.items():
        # Packages already handled (before the new agent joined) stay put;
        # only append the newly (re)assigned remaining packages.
        already_ids = {p["id"] for p in already_done}
        combined_assignments.setdefault(agent_id, [])
        combined_assignments[agent_id] = [
            p for p in combined_assignments[agent_id] if p["id"] in already_ids
        ] + pkgs

    new_sim_results = simulate_day(combined_assignments, warehouses, updated_agents)
    return updated_agents, combined_assignments, new_sim_results


def export_top_performer_csv(report, path):
    """Write the single most efficient agent's stats out as a CSV row."""
    best_agent = report.get("best_agent")
    if best_agent is None:
        return None

    stats = report[best_agent]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["agent_id", "packages_delivered", "total_distance", "efficiency"])
        writer.writerow([
            best_agent,
            stats["packages_delivered"],
            stats["total_distance"],
            stats["efficiency"],
        ])
    return path


# ---------------------------------------------------------------------------
# 7. CLI wiring
# ---------------------------------------------------------------------------

def parse_join_agent_arg(value):
    """Parse "A5:12,34@30" -> ("A5", (12.0, 34.0), 30)."""
    agent_part, index_part = value.split("@")
    agent_id, coords = agent_part.split(":")
    x_str, y_str = coords.split(",")
    return agent_id, (float(x_str), float(y_str)), int(index_part)


def main():
    parser = argparse.ArgumentParser(description="FastBox one-day delivery simulator.")
    parser.add_argument("input_json", help="Path to the input data JSON file.")
    parser.add_argument("-o", "--output", default="report.json",
                         help="Where to save the report (default: report.json).")
    parser.add_argument("--ascii", action="store_true",
                         help="Print an ASCII visualisation of routes.")
    parser.add_argument("--delays", action="store_true",
                         help="Simulate random delivery delays and include them in the report.")
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed, for reproducible --delays output.")
    parser.add_argument("--join-agent", metavar="AGENT_ID:X,Y@PACKAGE_INDEX",
                         help='A new agent joining mid-day, e.g. "A5:12,34@6" '
                              "means agent A5 starts at (12, 34) and is available "
                              "for the 7th package onward (0-indexed cutoff = 6).")
    parser.add_argument("--top-performer-csv", metavar="PATH",
                         help="Also export the best agent's stats to this CSV file.")
    args = parser.parse_args()

    raw = load_raw_json(args.input_json)
    warehouses, agents, packages = normalize_data(raw)

    assignments = assign_packages_to_agents(packages, warehouses, agents)

    rng = random.Random(args.seed)
    delay_config = {"chance": 0.3, "max_minutes": 20} if args.delays else None
    sim_results = simulate_day(assignments, warehouses, agents,
                                delay_config=delay_config, rng=rng)

    if args.join_agent:
        new_agent_id, new_agent_location, cutoff = parse_join_agent_arg(args.join_agent)
        agents, assignments, sim_results = add_agent_mid_day(
            agents, warehouses, packages, assignments, sim_results,
            new_agent_id, new_agent_location, cutoff,
        )
        print(f"[info] {new_agent_id} joined mid-day at {new_agent_location} "
              f"(effective from package index {cutoff}).")

    report = build_report(sim_results, include_delays=args.delays)
    sanity_check(report, total_packages=len(packages))

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4)

    print(json.dumps(report, indent=4))
    print(f"\nReport saved to {args.output}")

    if args.ascii:
        print("\n" + render_ascii_map(warehouses, agents, sim_results))

    if args.top_performer_csv:
        path = export_top_performer_csv(report, args.top_performer_csv)
        if path:
            print(f"Top performer exported to {path}")


if __name__ == "__main__":
    main()
