# IR Understanding Log

## Files Examined

1. Primary IR and Transform files:
   - `pythonstan/ir/ir_statements.py`: IR node definitions and hierarchy
   - `pythonstan/analysis/transform/three_address.py`: Three-address code transformation
   - `pythonstan/analysis/transform/ir.py`: IR transformation
   - `pythonstan/analysis/transform/block_cfg.py`: Block-level CFG construction
   - `pythonstan/analysis/transform/cfg.py`: Statement-level CFG construction
   - `pythonstan/graph/cfg/*`: CFG representation (base_block.py, edges.py, cfg.py)

2. Supporting files:
   - `docs/digests/transform-entrypoints.json`: Entry points for transformations
   - `docs/digests/file-symbols.jsonl`: File structure and symbols overview
   - `docs/digests/repo-tree.txt`: Repository file structure

## Key Insights

### IR Structure

- The IR is based on a hierarchy of statement types derived from `IRStatement`
- Core operations relevant to pointer analysis include:
  - Allocation operations (`IRAssign` with various expression types)
  - Variable operations (`IRCopy`)
  - Attribute operations (`IRLoadAttr`, `IRStoreAttr`)
  - Subscript operations (`IRLoadSubscr`, `IRStoreSubscr`)
  - Call operations (`IRCall`)
  - Return operations (`IRReturn`)
  - Module/scope operations (`IRModule`, `IRFunc`, `IRClass`)

### Three-Address Code Transformation

- `ThreeAddressTransformer` breaks down complex Python expressions into simpler forms
- Major transformations relevant to points-to analysis:
  - Object allocations become explicit temporary assignments
  - Method calls split into attribute load and function call
  - Comprehensions and unpacking expanded to loops and indexing

### Control Flow Graph Construction

- Two-level CFG construction:
  1. `BlockCFG` groups IR statements into basic blocks
  2. `CFG` creates statement-level CFG for fine-grained flow

### Allocation Sites

- Allocation sites are found primarily in:
  1. Object instantiation: `obj = Class()` → `$tmp = Class(); obj = $tmp`
  2. Container creation: `lst = [a,b,c]` → `$tmp = [a,b,c]; lst = $tmp`
  3. Function/class definition: `def func()` → `IRFunc`
  4. Lambda expressions: `lambda x: x` → function allocation
  5. Method binding: `obj.method` → bound method allocation
  6. Generator: `yield` → generator frame allocation

### Call Sites

- Call sites are represented by `IRCall` nodes
- Direct function calls have known targets at transformation time
- Method calls require method binding and then call
- Call edges in CFG connect call sites to function entries

## Identified Gaps

1. **Dynamic attribute access**: No specialized IR nodes for `getattr`/`setattr`
2. **Method binding**: Not explicitly represented as allocation
3. **Generator frames**: Implicit rather than explicit allocation
4. **Container element sensitivity**: Uniform field modeling needed
5. **Stable site IDs**: Need standardized scheme for allocation site identification

## Proposals

1. Standardized allocation site ID scheme:
   ```
   alloc_id := f"{file}:{lineno}:{col}:{kind}"
   call_id := f"{file}:{lineno}:{col}:call"
   fallback := f"{file_stem}:{op}:{hash(uid)%2**32:x}"
   ```

2. Unified field sensitivity model:
   - Lists/sets/tuples: element-insensitive with "elem" field
   - Dicts: key-insensitive with "value" field
   - Objects: attribute-name-sensitive

3. Extension points for specialized handling of:
   - Dynamic attribute access
   - Descriptor protocol
   - Special container methods
   - Standard library modeling