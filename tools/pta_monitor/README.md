# PTA Monitor - Interactive Pointer Analysis Debugger

A production-grade NiceGUI web application for debugging and monitoring k-CFA pointer analysis execution. Designed to help diagnose **why absolute call edges are under-produced** and understand points-to flow.

## Quick Start

```bash
# Install dependencies (if nicegui is not installed)
pip install nicegui

# From the project root, run the monitor
python scripts/run_pta_monitor.py

# Or analyze a specific file
python scripts/run_pta_monitor.py --target path/to/file.py

# Analyze with specific policy
python scripts/run_pta_monitor.py --target project/ --policy 2-call-site
```

Open your browser to `http://127.0.0.1:8080` to access the UI.

## Features

### Top Toolbar
- **Module Selection**: Dropdown to select the current module (displays file name)
- **Iteration Display**: Live iteration counter during analysis
- **Analysis Controls**: Start/Stop buttons with configurable path and policy
- **K-Lookback**: Input to set iteration lookback and compute delta
- **Search**: Filter for variables/callsites/functions

### Main Panels

#### Left Panel: Source Code Viewer
- Displays Python source code for the selected module
- Syntax highlighted with Pygments

#### Right Panel: IR Viewer
- Lists IR statements with stable indices
- Click a statement to populate the Inspector tab
- Call statements highlighted in blue
- Clicking a call shows callsite resolution details

### Bottom Tabs

#### Tab 1: Selection Inspector
Shows raw details for the selected IR statement:
- Statement text and location
- Variables involved with their points-to sets
- Call resolution details (if applicable):
  - Callee points-to size
  - Resolved call edges
  - Call failures with reasons
- Related constraints
- PFG neighborhood

#### Tab 2: Delta (iter-k → iter)
Shows changes since iteration k:
- Points-to additions (variable → added objects)
- New call edges
- Call failures
- Constraints applied counts + examples
- New scopes analyzed
- PFG activity summary

#### Tab 3: Hot Spots
Highlights problematic areas:
- Top callsites by failures
- Variables with empty points-to
- Functions with objects but no incoming calls
- Modules with low call edge coverage

#### Tab 4: Unknowns
Unknown/unmodeled elements:
- Unknown tracker summary
- Detailed reports of unknown imports, callees, etc.

### Right Sidebar

#### Call Graph
- Interactive Cytoscape.js visualization with Dagre layout
- Filter by module or "around selection"
- Click nodes/edges for details
- Drag, zoom, and pan support

#### Pointer Flow Graph (PFG)
- Ego graph centered around selected variable
- Shows normal, inherit, and instance edges
- Configurable radius and max nodes

## Architecture

```
tools/pta_monitor/
├── __init__.py          # Package exports
├── backend.py           # Analysis runner and query APIs
├── delta_tracker.py     # Iteration-aware delta tracking
├── models.py            # Data models (text-first format)
├── ui.py               # NiceGUI frontend
├── demo_program.py     # Demo test file
└── README.md           # This file

scripts/
└── run_pta_monitor.py  # Entry script
```

## Key Design Decisions

### Text-First Approach
All data is displayed as raw `str()` outputs and Python dict/list dumps with indentation. This minimizes preprocessing and formatting overhead, focusing on debuggability over aesthetics.

### Direct State Access
The UI queries **directly from `PointerAnalysisState`** for authoritative data, not just from exported debug files. This ensures accurate, real-time information.

### Event-Driven Delta Tracking
Uses a lightweight event listener mechanism where points-to updates, call edges, failures, constraints, and PFG activations are recorded with `(iteration, payload)`. Deltas are computed by aggregating events between `iter-k+1` and `iter`.

### Observer Pattern
The `DebugMonitor` supports an observer API (`MonitorObserver`) that forwards events to the `DeltaTracker`. This enables real-time updates without modifying the core solver.

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `--target` | None | Python file or directory to analyze |
| `--policy` | `2-call-site` | Context sensitivity policy |
| `--max-iterations` | 100000 | Maximum solver iterations |
| `--port` | 8080 | Web server port |
| `--host` | `127.0.0.1` | Web server host |
| `--debug` | False | Enable debug logging |
| `--reload` | False | Enable auto-reload for development |

## Debugging Call Edge Issues

The monitor is optimized for debugging **missing call edges**. Here's the typical workflow:

1. **Start analysis** on your target project
2. **Check the Delta tab** with k=0 to see all call edges created
3. **Look at Hot Spots** for callsites with failures
4. **Click on failed callsites** in the IR to see:
   - Is the callee variable's points-to set empty?
   - Are there non-callable objects in the points-to set?
   - Is there a translation/modeling error?
5. **Use the PFG graph** to trace where propagation stops
6. **Check Unknowns tab** for missing imports or unmodeled APIs

## Example Usage

### Analyzing the Demo Program

```bash
python scripts/run_pta_monitor.py --target tools/pta_monitor/demo_program.py
```

The demo program includes:
- Class inheritance (Animal → Dog, Cat)
- Polymorphic method calls
- Factory functions
- Higher-order functions
- Method dispatch through dictionaries

Expected call edges include:
- `main` → `Dog.__init__`, `Cat.__init__`
- `main` → `dog.speak`, `cat.speak`
- `main` → `greet_animal` → `animal.speak` (polymorphic)
- `main` → `create_pet` → `Dog()`/`Cat()`
- `main` → `higher_order_func` → `double`/`square`
- `main` → `Calculator.calculate` → method dispatch

### Analyzing Flask

```bash
python scripts/run_pta_monitor.py --target path/to/flask/ --policy 2-call-site
```

For Flask, focus on:
- Route handler registration
- Decorator-based call patterns
- Dynamic dispatch through Werkzeug

