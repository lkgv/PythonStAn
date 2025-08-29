# PythonStAn

<pre>
<font color="deepskyblue"> ______            _                    </font><font color="black">_                      </font>
<font color="deepskyblue">(_____ \      _   | |                  </font><font color="black">| |   _      </font><font color="red">/\         </font>
<font color="deepskyblue"> _____) _   _| |_ | | _   ___  ____</font><font color="black">     \ \ | |_</font><font color="red">   /  \  ____  </font>
<font color="deepskyblue">|  ____| | | |  _)| || \ / _ \|  _ \</font><font color="black">     \ \|  _) </font><font color="red">/ /\ \|  _ \ </font>
<font color="yellow">| |    | |_| | |__| | | | |_| | | | |</font><font color="black">_____) | |__</font><font color="red">| |__| | | | |</font>
<font color="yellow">|_|     \__  |\___|_| |_|\___/|_| |_</font><font color="black">(______/ \___</font><font color="red">|______|_| |_|</font>
<font color="yellow">       (____/                                                  </font>
</pre>

PythonStAn is a comprehensive **Python** **Static** **Analysis** framework designed for advanced program analysis and research.

---

## Overview

PythonStAn provides a robust infrastructure for performing various types of static analysis on Python programs. The framework supports multiple analysis domains including:

- **Dataflow Analysis**: Liveness analysis, reaching definition analysis, and def-use chains
- **Pointer Analysis**: k-CFA based pointer analysis with configurable context sensitivity
- **Control Flow Analysis**: CFG generation, interprocedural control flow graphs (ICFG)
- **Abstract Interpretation**: AI-based analysis with configurable abstract domains
- **Scope Analysis**: Module and function scope management

---

## Architecture

The PythonStAn framework follows a layered architecture with clear separation of concerns:

### Core Components

#### 1. World Management Layer
```
pythonstan/world/
├── world.py           # Global singleton managing analysis environment
├── pipeline.py        # Main analysis execution pipeline
├── config.py          # Configuration management
├── scope_manager.py   # Module and scope management
├── namespace.py       # Namespace resolution
└── import_manager.py  # Import dependency tracking
```

The **World** class serves as a central coordinator, maintaining:
- Scope and module management
- Namespace resolution
- Class hierarchy information
- Import dependency tracking

#### 2. Analysis Framework
```
pythonstan/analysis/
├── analysis.py        # Base analysis interfaces and configuration
├── dataflow/          # Dataflow analysis implementations
├── pointer/           # Pointer analysis with k-CFA
├── ai/                # Abstract interpretation framework
├── scope/             # Scope and closure analysis
└── transform/         # IR transformation pipeline
```

**Analysis Types**:
- **Transform**: IR transformations (AST → Three-address → CFG → SSA)
- **Dataflow Analysis**: Traditional dataflow frameworks (liveness, reaching definitions)
- **Pointer Analysis**: Context-sensitive pointer analysis using k-CFA
- **Abstract Interpretation**: Configurable abstract domains for program properties

#### 3. Intermediate Representation (IR)
```
pythonstan/ir/
├── ir_statements.py   # IR statement definitions
└── ir_visitor.py      # Visitor pattern for IR traversal
```

**IR Pipeline**:
1. **AST** → Parse Python source code
2. **Three-Address Code** → Normalize expressions and control flow
3. **CFG** → Build control flow graphs
4. **SSA** → Static single assignment form (TODO)

#### 4. Analysis Execution Pipeline

The pipeline orchestrates analysis execution:

1. **Module Discovery**: Parse entry file and discover dependencies
2. **IR Generation**: Transform source to intermediate representations
3. **Graph Construction**: Build CFG, call graphs, and ICFG
4. **Analysis Execution**: Run configured analyses in dependency order
5. **Result Collection**: Aggregate and store analysis results

---

## How to Use

### Prerequisites

```bash
# Install dependencies
poetry install

# Or using pip
pip install -r requirements.txt
```

### Primary Analysis Scripts

#### 1. General Pipeline Runner (`scripts/do_pipeline.py`)

The main entry point for running customizable analysis pipelines.

**Basic Usage:**
```python
python scripts/do_pipeline.py
```

**Configuration:**
The script uses a configuration dictionary to specify:

```python
CONFIG = {
    "filename": "/path/to/your/file.py",
    "project_path": "/path/to/project/root",
    "library_paths": [
        "/usr/lib/python3.9",
        "/usr/lib/python3.9/site-packages"
    ],
    "analysis": [
        {
            "name": "liveness",
            "id": "LivenessAnalysis",
            "description": "liveness analysis",
            "prev_analysis": ["cfg"],
            "options": {
                "type": "dataflow analysis",
                "ir": "ssa"
            }
        }
    ]
}
```

**Available Analysis Types:**
- `"dataflow analysis"`: Liveness, reaching definitions
- `"pointer analysis"`: k-CFA pointer analysis
- `"transform"`: IR transformations
- `"inter-procedure"`: Interprocedural analysis

#### 2. Pointer Analysis Demo (`scripts/do_pa.py`)

Specialized script for demonstrating k-CFA pointer analysis capabilities.

**Basic Usage:**
```bash
python scripts/do_pa.py
```

**Features:**
- Multiple test cases (interprocedural, OOP, higher-order functions)
- Configurable k-CFA parameters
- Comprehensive result reporting
- Field sensitivity options

**Test Cases Included:**
1. **Simple Interprocedural**: Basic function calls and data flow
2. **Complex OOP**: Class hierarchies, method calls, object interactions
3. **Higher-Order Functions**: Closures, function composition, callbacks
4. **Complex Containers**: Nested data structures, container operations
5. **Exception Handling**: Try/catch blocks, error propagation

**Configuration Options:**
```python
"options": {
    "type": "pointer analysis",
    "k": 2,                    # Context sensitivity depth
    "obj_depth": 2,           # Object allocation depth
    "field_sensitivity": "attr", # Field sensitivity mode
    "verbose": True
}
```

### Output and Results

Analysis results are accessible through:
```python
world = pipeline.get_world()
scopes = world.scope_manager.get_scopes()
ir_forms = world.scope_manager.get_ir(scope, "three address form")
results = pipeline.analysis_manager.get_results("analysis_name")
```

Results typically include:
- **Points-to information**: Variable → object mappings
- **Call graph data**: Function call relationships
- **Dataflow results**: Live variables, reaching definitions
- **Statistics**: Analysis performance metrics

## License

This project is licensed under the terms specified in the LICENSE file.
