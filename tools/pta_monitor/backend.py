"""Backend service for PTA Monitor.

Provides:
- Analysis runner (background thread)
- Query APIs for UI
- Direct access to PointerAnalysisState for authoritative data
"""

import os
import sys
import time
import threading
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from collections import defaultdict

from pythonstan.analysis.pointer.kcfa import PointerAnalysis, PointerAnalysisState

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from .models import (
    ModuleInfo, IRStatement, IRBlockInfo, PointsToInfo, CallSiteInfo,
    GraphNode, GraphEdge, AnalysisStatus, HotSpots, DeltaSummary
)
from queue import Queue
from .delta_tracker import DeltaTracker, DeltaTrackerMonitorAdapter, DeltaTrackerObserver

logger = logging.getLogger(__name__)


class PTAMonitorBackend:
    """Backend service for the PTA Monitor UI.
    
    Manages analysis execution and provides query APIs for the frontend.
    Queries directly from PointerAnalysisState for authoritative data.
    """
    
    _state: PointerAnalysisState
    _analysis: PointerAnalysis
    
    def __init__(self):
        """Initialize backend."""
        self._analysis = None
        self._solver = None
        self._state = None
        self._world = None
        
        # Delta tracker for iteration-aware debugging
        self.delta_tracker = DeltaTracker()
        
        # Analysis thread state
        self._analysis_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._is_paused = False
        self._should_stop = False
        self._start_time = 0.0
        
        # Default configuration values
        self._target_path = str(Path(__file__).parent / "demo_program.py")
        self._context_policy = "2-cfa"
        self._max_iterations = 100000
        self._enable_debug = True
        
        # Cache for expensive computations
        self._module_cache: Dict[str, ModuleInfo] = {}
        self._call_site_cache: Dict[str, CallSiteInfo] = {}
        
        # Lock for thread-safe access
        self._lock = threading.Lock()
    
    def set_default_values(self, target_path: str, context_policy: str = "2-call-site", max_iterations: int = 100000, enable_debug: bool = True):
        self._target_path = target_path
        self._context_policy = context_policy
        self._max_iterations = max_iterations
        self._enable_debug = enable_debug
    
    def get_default_target_path(self) -> str:
        return self._target_path
    
    def get_default_context_policy(self) -> str:
        return self._context_policy
    
    def get_default_max_iterations(self) -> int:
        return self._max_iterations
    
    def get_default_enable_debug(self) -> bool:
        return self._enable_debug
    
    def start_analysis(
        self,
        target_path: str,
        context_policy: str = "2-cfa",
        max_iterations: int = 100000,
        enable_debug: bool = True
    ) -> bool:
        """Start pointer analysis on target.
        
        Args:
            target_path: Path to Python file or directory
            context_policy: Context sensitivity policy
            max_iterations: Maximum solver iterations
            enable_debug: Enable debug monitoring
            
        Returns:
            True if analysis started successfully
        """
        if self._is_running:
            logger.warning("Analysis already running")
            return False
        
        # Clear caches
        self._module_cache.clear()
        self._call_site_cache.clear()
        self.delta_tracker.clear()
        
        # Start analysis in background thread
        self._analysis_thread = threading.Thread(
            target=self._run_analysis,
            args=(target_path, context_policy, max_iterations, enable_debug),
            daemon=True
        )
        self._is_running = True
        self._is_paused = False
        self._should_stop = False
        self._start_time = time.time()
        self._analysis_thread.start()
        
        return True
    
    def _run_analysis(
        self,
        target_path: str,
        context_policy: str,
        max_iterations: int,
        enable_debug: bool
    ) -> None:
        """Run analysis in background thread."""
        try:
            import site
            from pythonstan.world.pipeline import Pipeline
            from pythonstan.world import World
            
            # Resolve target path
            target = Path(target_path).resolve()
            if target.is_file():
                filename = str(target)
                project_path = str(target.parent)
            else:
                # Directory - find main entry point
                filename = None
                for candidate in ['__main__.py', 'main.py', 'app.py', '__init__.py']:
                    candidate_path = target / candidate
                    if candidate_path.exists():
                        filename = str(candidate_path)
                        break
                if filename is None:
                    # Find first .py file
                    py_files = list(target.glob("*.py"))
                    if py_files:
                        filename = str(py_files[0])
                    else:
                        logger.error(f"No Python files found in {target}")
                        self._is_running = False
                        return
                project_path = str(target)
            
            # Get library paths
            library_paths = [os.path.dirname(os.__file__)]
            for path in site.getsitepackages():
                library_paths.append(path)
            user_site = site.getusersitepackages()
            if user_site:
                library_paths.append(user_site)
            
            # Configure pipeline with pointer analysis
            config = {
                "filename": filename,
                "project_path": project_path,
                "library_paths": library_paths,
                "analysis": [
                    {
                        "name": "pointer",
                        "id": "PointerAnalysis",
                        "description": "k-CFA pointer analysis",
                        "prev_analysis": [],
                        "options": {
                            "type": "pointer analysis",
                            "context_policy": context_policy,
                            "max_iterations": max_iterations,
                            "enable_debug_monitor": enable_debug,
                            "track_object_flow": True,
                            "track_pfg_activation": True,
                            "export_debug_data": False,
                            "project_path": project_path,
                        }
                    }
                ]
            }
            
            logger.info(f"Starting analysis on {filename}")
            
            # Create pipeline and run
            pipeline = Pipeline(config=config)
            
            # Get references before running
            self._world = pipeline.get_world()
            
            # Run pipeline
            pipeline.run()
            
            # Get analysis components after run
            analysis_manager = pipeline.analysis_manager
            
            # Find the pointer analysis driver
            #for driver in analysis_manager.drivers.values():
            #    if hasattr(driver, 'solver'):
            
            driver = analysis_manager.analyzers['pointer']
            self._analysis = driver
            self._solver = driver.solver
            self._state = driver.state
            
            # Hook delta tracker to monitor using observer pattern
            if hasattr(driver, 'debug_monitor') and driver.debug_monitor:
                observer = DeltaTrackerObserver(self.delta_tracker)
                driver.debug_monitor.add_observer(observer)
                logger.info("Connected DeltaTracker observer to DebugMonitor")
            # break
            
            logger.info("Analysis complete")
            
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._is_running = False
    
    
    def stop_analysis(self) -> None:
        """Stop the running analysis."""
        self._should_stop = True
        self._is_running = False
    
    def get_status(self) -> AnalysisStatus:
        """Get current analysis status."""
        if self._state is None:
            return AnalysisStatus(
                is_running=self._is_running,
                is_paused=self._is_paused,
                current_iteration=0,
                worklist_size=0,
                num_call_edges=0,
                num_plain_call_edges=0,
                num_pfg_edges=0,
                num_variables=0,
                num_objects=0,
                elapsed_time=0.0
            )
        
        return AnalysisStatus(
            is_running=self._is_running,
            is_paused=self._is_paused,
            current_iteration=self.delta_tracker.current_iteration,
            worklist_size=len(self._state._worklist) if hasattr(self._state, '_worklist') else 0,
            num_call_edges=self._state._call_graph.get_number_of_edges() if self._state._call_graph else 0,
            num_plain_call_edges=self._state._call_graph.num_plain_edges() if hasattr(self._state._call_graph, 'num_plain_edges') else 0,
            num_pfg_edges=len(self._state._pointer_flow_graph.edges) if self._state._pointer_flow_graph else 0,
            num_variables=len(self._state._env) if self._state._env else 0,
            num_objects=len(self._state._heap.objects) if self._state._heap else 0,
            elapsed_time=time.time() - self._start_time if self._start_time else 0.0
        )
    
    def get_modules(self) -> List[Dict[str, str]]:
        """Get list of analyzed modules."""
        if self._world is None:
            return []
        
        modules = []
        try:
            scope_manager = self._world.scope_manager
            for scope in scope_manager.get_scopes():
                if hasattr(scope, 'get_qualname'):
                    qualname = scope.get_qualname()
                    filename = getattr(scope, 'filename', '') or qualname
                    # Only include top-level modules, not nested scopes
                    if '.' not in qualname or qualname.count('.') <= 1:
                        modules.append({
                            'qualname': qualname,
                            'filename': str(filename),
                            'display_name': Path(str(filename)).name if filename else qualname
                        })
        except Exception as e:
            logger.error(f"Error getting modules: {e}")
        
        return modules
    
    def get_module_info(self, qualname: str) -> Optional[ModuleInfo]:
        """Get detailed module information.
        
        Args:
            qualname: Module qualified name
            
        Returns:
            ModuleInfo with source, IR, and structure (block-formatted)
        """
        # Check cache
        if qualname in self._module_cache:
            return self._module_cache[qualname]
        
        if self._world is None:
            return None
        
        try:
            scope_manager = self._world.scope_manager
            
            # Find the scope
            target_scope = scope_manager.get_module(qualname)
            if target_scope is None:
                return None
            
            # Get source code - prefer file content, fall back to AST unparse
            import ast
            filename = getattr(target_scope, 'filename', None)
            source_code = ""
            if filename and Path(filename).exists():
                try:
                    source_code = Path(filename).read_text()
                except Exception:
                    pass
            
            if not source_code:
                try:
                    source_code = ast.unparse(target_scope.get_ast())
                except Exception:
                    source_code = "# Could not retrieve source code"
            
            # Build block-structured IR text using CFG traversal
            ir_stmts: List[IRStatement] = []
            ir_lines: List[str] = []
            source_to_ir: Dict[int, List[int]] = {}
            ir_to_source: Dict[int, int] = {}
            line_counter = [1]  # Use list to allow mutation in nested function
            
            def get_lineno(stmt) -> Optional[int]:
                """Extract source line number from IR statement if available."""
                if hasattr(stmt, 'stmt') and hasattr(stmt.stmt, 'lineno'):
                    return stmt.stmt.lineno
                if hasattr(stmt, 'lineno'):
                    return stmt.lineno
                # Try to get from AST
                try:
                    ast_node = stmt.get_ast() if hasattr(stmt, 'get_ast') else None
                    if ast_node and hasattr(ast_node, 'lineno'):
                        return ast_node.lineno
                except Exception:
                    pass
                return None
            
            def build_ir_for_scope(scope, indent_level: int = 0, scope_header: str = ""):
                """Build IR text for a scope using CFG traversal.
                
                Produces basic-block formatted output like:
                    bb0:
                        $const0 = 2
                        $const1 = 3
                    bb1:
                        $tmp0 = $const0 + $const1
                """
                from pythonstan.ir import IRFunc, IRClass, IRScope, Label
                
                indent = "    " * indent_level
                scope_name = scope.get_qualname() if hasattr(scope, 'get_qualname') else str(scope)
                
                # Add scope header if provided
                if scope_header:
                    ir_lines.append(f"{indent}{scope_header}")
                    line_counter[0] += 1
                
                # Try to get CFG (block cfg preferred for basic block structure)
                cfg = scope_manager.get_ir(scope, 'block cfg')
                if cfg is None:
                    cfg = scope_manager.get_ir(scope, 'cfg')
                
                if cfg is not None and hasattr(cfg, 'get_entry'):
                    # CFG traversal with BFS to maintain ordering
                    entry = cfg.get_entry()
                    visited = set()
                    block_order = []  # Track block order for consistent output
                    q = Queue()
                    q.put(entry)
                    
                    while not q.empty():
                        blk = q.get()
                        if blk in visited:
                            continue
                        visited.add(blk)
                        block_order.append(blk)
                        
                        # Add successors to queue
                        if hasattr(cfg, 'succs_of'):
                            for succ in cfg.succs_of(blk):
                                if succ not in visited:
                                    q.put(succ)
                    
                    # Process blocks in order
                    for blk in block_order:
                        block_id = blk.get_idx() if hasattr(blk, 'get_idx') else id(blk) % 10000
                        
                        # Add block label
                        ir_lines.append(f"{indent}bb{block_id}:")
                        bb_line = line_counter[0]
                        line_counter[0] += 1
                        
                        # Process statements in block
                        for stmt in blk.get_stmts():
                            if isinstance(stmt, Label):
                                continue  # Skip Label statements, we added bb label above
                            
                            stmt_str = str(stmt)
                            lineno = get_lineno(stmt)
                            ir_line_num = line_counter[0]
                            
                            # Check if it's a call statement
                            is_call = (
                                'Call' in type(stmt).__name__ or 
                                'Call' in stmt_str or 
                                ('(' in stmt_str and '=' in stmt_str and 'Subscr' not in stmt_str)
                            )
                            
                            # Handle nested scope statements (IRFunc, IRClass)
                            if isinstance(stmt, IRFunc):
                                # Function definition
                                func_name = stmt.name if hasattr(stmt, 'name') else str(stmt)
                                func_args = ""
                                if hasattr(stmt, 'args') and stmt.args:
                                    args_list = [arg.arg for arg in (stmt.args.args or [])]
                                    func_args = ", ".join(args_list)
                                
                                ir_lines.append(f"{indent}    def {func_name}({func_args}):")
                                line_counter[0] += 1
                                
                                # Record the function definition statement
                                ir_stmts.append(IRStatement(
                                    index=len(ir_stmts),
                                    text=f"def {func_name}({func_args}):",
                                    is_call=False,
                                    scope_qualname=scope_name,
                                    line_number=lineno,
                                    block_id=block_id,
                                    indent_level=indent_level
                                ))
                                
                                if lineno:
                                    if lineno not in source_to_ir:
                                        source_to_ir[lineno] = []
                                    source_to_ir[lineno].append(ir_line_num)
                                    ir_to_source[ir_line_num] = lineno
                                
                                # Recursively process subscope
                                build_ir_for_scope(stmt, indent_level + 2)
                                
                            elif isinstance(stmt, IRClass):
                                # Class definition
                                cls_name = stmt.name if hasattr(stmt, 'name') else str(stmt)
                                bases_str = ""
                                if hasattr(stmt, 'bases') and stmt.bases:
                                    bases_str = ", ".join(b.id for b in stmt.bases if hasattr(b, 'id'))
                                
                                header = f"class {cls_name}({bases_str}):" if bases_str else f"class {cls_name}:"
                                ir_lines.append(f"{indent}    {header}")
                                line_counter[0] += 1
                                
                                ir_stmts.append(IRStatement(
                                    index=len(ir_stmts),
                                    text=header,
                                    is_call=False,
                                    scope_qualname=scope_name,
                                    line_number=lineno,
                                    block_id=block_id,
                                    indent_level=indent_level
                                ))
                                
                                if lineno:
                                    if lineno not in source_to_ir:
                                        source_to_ir[lineno] = []
                                    source_to_ir[lineno].append(ir_line_num)
                                    ir_to_source[ir_line_num] = lineno
                                
                                # Recursively process subscope
                                build_ir_for_scope(stmt, indent_level + 2)
                                
                            else:
                                # Regular IR statement
                                ir_lines.append(f"{indent}    {stmt_str}")
                                line_counter[0] += 1
                                
                                ir_stmts.append(IRStatement(
                                    index=len(ir_stmts),
                                    text=stmt_str,
                                    is_call=is_call,
                                    scope_qualname=scope_name,
                                    line_number=lineno,
                                    block_id=block_id,
                                    indent_level=indent_level
                                ))
                                
                                # Build source-to-IR mapping
                                if lineno:
                                    if lineno not in source_to_ir:
                                        source_to_ir[lineno] = []
                                    source_to_ir[lineno].append(ir_line_num)
                                    ir_to_source[ir_line_num] = lineno
                        
                        # Add empty line between blocks for readability (optional)
                        if len(block_order) > 1:
                            ir_lines.append("")
                            line_counter[0] += 1
                else:
                    # Fallback: use flat IR list if CFG not available
                    ir_list = scope_manager.get_ir(scope, 'ir')
                    if ir_list:
                        ir_lines.append(f"{indent}bb0:")
                        line_counter[0] += 1
                        
                        for idx, stmt in enumerate(ir_list):
                            stmt_str = str(stmt)
                            lineno = get_lineno(stmt)
                            ir_line_num = line_counter[0]
                            is_call = 'Call' in stmt_str or ('(' in stmt_str and '=' in stmt_str)
                            
                            ir_lines.append(f"{indent}    {stmt_str}")
                            line_counter[0] += 1
                            
                            ir_stmts.append(IRStatement(
                                index=idx,
                                text=stmt_str,
                                is_call=is_call,
                                scope_qualname=scope_name,
                                line_number=lineno,
                                block_id=0,
                                indent_level=indent_level
                            ))
                            
                            if lineno:
                                if lineno not in source_to_ir:
                                    source_to_ir[lineno] = []
                                source_to_ir[lineno].append(ir_line_num)
                                ir_to_source[ir_line_num] = lineno
            
            # Build IR for the main scope (module level)
            module_name = target_scope.get_qualname() if hasattr(target_scope, 'get_qualname') else qualname
            ir_lines.append(f"# Module: {module_name}")
            line_counter[0] += 1
            
            build_ir_for_scope(target_scope, 0)
            
            # Get functions and classes from subscopes
            functions = []
            classes = []
            subscopes = scope_manager.subscopes.get(target_scope, [])
            for sub in subscopes:
                sub_name = sub.get_qualname() if hasattr(sub, 'get_qualname') else str(sub)
                sub_type = type(sub).__name__
                if 'Func' in sub_type:
                    functions.append(sub_name)
                elif 'Class' in sub_type:
                    classes.append(sub_name)
            
            module_info = ModuleInfo(
                qualname=qualname,
                filename=str(filename) if filename else qualname,
                display_name=Path(str(filename)).name if filename else qualname,
                source_code=source_code,
                ir_statements=ir_stmts,
                ir_text='\n'.join(ir_lines),
                functions=functions,
                classes=classes,
                source_to_ir=source_to_ir,
                ir_to_source=ir_to_source
            )
            
            # Cache and return
            self._module_cache[qualname] = module_info
            return module_info
            
        except Exception as e:
            logger.error(f"Error getting module info for {qualname}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_points_to(self, variable_str: str) -> PointsToInfo:
        """Get points-to information for a variable.
        
        Args:
            variable_str: String representation of the variable
            
        Returns:
            PointsToInfo with points-to set details
        """
        if self._state is None:
            return PointsToInfo(variable_str=variable_str, size=0, objects=[])
        
        objects = []
        size = 0
        
        # Search for matching variable in env
        for var, pts in self._state._env.items():
            var_str = str(var)
            if variable_str in var_str or var_str == variable_str:
                size = len(pts)
                for obj in pts:
                    obj_info = {
                        'str': str(obj)[:200],
                        'kind': obj.kind.value if hasattr(obj, 'kind') else 'unknown',
                        'alloc_site': str(obj.alloc_site)[:100] if hasattr(obj, 'alloc_site') else '',
                        'context': str(obj.context)[:100] if hasattr(obj, 'context') else ''
                    }
                    objects.append(obj_info)
                break
        
        return PointsToInfo(
            variable_str=variable_str,
            size=size,
            objects=objects
        )
    
    def get_callsite_info(self, call_site_str: str) -> Optional[CallSiteInfo]:
        """Get information about a call site.
        
        Args:
            call_site_str: Call site identifier
            
        Returns:
            CallSiteInfo with resolution details
        """
        if self._state is None:
            return None
        
        # Search through call constraints
        from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
        
        resolved_edges = []
        failures = []
        callee_var = ""
        callee_pts_size = 0
        
        try:
            call_constraints = self._state.constraints.get_by_type(CallConstraint)
            
            for constraint in call_constraints:
                if call_site_str in str(constraint.call_site):
                    callee_var = str(constraint.callee)
                    
                    # Get callee points-to
                    # Need to find the contextualized variable
                    for var, pts in self._state._env.items():
                        if str(constraint.callee.name) in str(var):
                            callee_pts_size = len(pts)
                            break
                    
                    break
            
            # Get call edges from call graph
            if self._state._call_graph:
                for edge in self._state._call_graph.edges:
                    edge_site = str(edge.callsite.content) if hasattr(edge.callsite, 'content') else str(edge.callsite)
                    if call_site_str in edge_site:
                        resolved_edges.append({
                            'caller': str(edge.callsite),
                            'callee': str(edge.callee.stmt.get_qualname() if hasattr(edge.callee.stmt, 'get_qualname') else edge.callee),
                            'kind': edge.kind.value if hasattr(edge.kind, 'value') else str(edge.kind)
                        })
            
            # Get failures from delta tracker
            from .delta_tracker import DeltaTracker
            from .models import DeltaEventType
            for event in self.delta_tracker._events.get(DeltaEventType.CALL_FAILED, []):
                if call_site_str in event.data.get('call_site', ''):
                    failures.append(event.data)
                    
        except Exception as e:
            logger.error(f"Error getting callsite info: {e}")
        
        return CallSiteInfo(
            call_site_str=call_site_str,
            callee_var=callee_var,
            callee_pts_size=callee_pts_size,
            resolved_edges=resolved_edges,
            failures=failures
        )
    
    def get_ir_statement_info(self, module_qualname: str, stmt_index: int) -> Dict[str, Any]:
        """Get detailed information for an IR statement.
        
        Args:
            module_qualname: Module qualified name
            stmt_index: Index of the IR statement
            
        Returns:
            Dictionary with statement details (text-first format)
        """
        result = {
            'statement': '',
            'index': stmt_index,
            'module': module_qualname,
            'variables': [],
            'points_to': {},
            'call_info': None,
            'constraints': [],
            'pfg_neighbors': {}
        }
        
        if self._world is None or self._state is None:
            return result
        
        try:
            scope_manager = self._world.scope_manager
            
            # Find scope and statement
            for scope in scope_manager.get_scopes():
                if hasattr(scope, 'get_qualname') and scope.get_qualname() == module_qualname:
                    ir_list = list(scope_manager.get_ir(scope, 'ir'))
                    if 0 <= stmt_index < len(ir_list):
                        stmt = ir_list[stmt_index]
                        result['statement'] = str(stmt)
                        
                        # Extract variable references from statement string
                        stmt_str = str(stmt)
                        # Find relevant variables in env
                        for var, pts in self._state._env.items():
                            var_str = str(var)
                            var_name = var.content.name if hasattr(var, 'content') and hasattr(var.content, 'name') else ''
                            if var_name and var_name in stmt_str:
                                result['variables'].append(var_str)
                                result['points_to'][var_str] = {
                                    'size': len(pts),
                                    'objects': [str(o)[:100] for o in list(pts)[:10]]
                                }
                        
                        # Check if it's a call
                        if 'Call' in stmt_str or ('(' in stmt_str and '=' in stmt_str):
                            result['call_info'] = {
                                'is_call': True,
                                'statement': stmt_str
                            }
                    break
                    
        except Exception as e:
            logger.error(f"Error getting IR statement info: {e}")
        
        return result
    
    def get_call_graph_data(
        self,
        module_filter: Optional[str] = None,
        around_node: Optional[str] = None,
        max_nodes: int = 100
    ) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Get call graph data for visualization.
        
        Args:
            module_filter: Filter to specific module
            around_node: Center graph around specific node
            max_nodes: Maximum number of nodes to return
            
        Returns:
            Tuple of (nodes, edges) for Cytoscape
        """
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        seen_nodes: Set[str] = set()
        
        if self._state is None or self._state._call_graph is None:
            return nodes, edges
        
        try:
            call_graph = self._state._call_graph
            
            for edge in call_graph.edges:
                if len(nodes) >= max_nodes:
                    break
                
                # Get caller/callee names
                caller_str = str(edge.callsite.scope.stmt.get_qualname() if hasattr(edge.callsite.scope, 'stmt') and hasattr(edge.callsite.scope.stmt, 'get_qualname') else edge.callsite)
                callee_str = str(edge.callee.stmt.get_qualname() if hasattr(edge.callee.stmt, 'get_qualname') else edge.callee)
                
                # Apply filters
                if module_filter:
                    if module_filter not in caller_str and module_filter not in callee_str:
                        continue
                
                if around_node:
                    if around_node not in caller_str and around_node not in callee_str:
                        continue
                
                # Add nodes
                caller_id = f"cg_{hash(caller_str) % 10000}"
                callee_id = f"cg_{hash(callee_str) % 10000}"
                
                if caller_id not in seen_nodes:
                    nodes.append(GraphNode(
                        id=caller_id,
                        label=caller_str.split('.')[-1] if '.' in caller_str else caller_str,
                        node_type='function',
                        data={'full_name': caller_str}
                    ))
                    seen_nodes.add(caller_id)
                
                if callee_id not in seen_nodes:
                    nodes.append(GraphNode(
                        id=callee_id,
                        label=callee_str.split('.')[-1] if '.' in callee_str else callee_str,
                        node_type='function',
                        data={'full_name': callee_str}
                    ))
                    seen_nodes.add(callee_id)
                
                # Add edge
                edges.append(GraphEdge(
                    source=caller_id,
                    target=callee_id,
                    edge_type='call',
                    data={
                        'call_site': str(edge.callsite.content)[:50] if hasattr(edge.callsite, 'content') else '',
                        'kind': edge.kind.value if hasattr(edge.kind, 'value') else ''
                    }
                ))
                
        except Exception as e:
            logger.error(f"Error getting call graph: {e}")
        
        return nodes, edges
    
    def get_pfg_data(
        self,
        around_node: Optional[str] = None,
        max_nodes: int = 50,
        radius: int = 2
    ) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Get PFG data for visualization (ego graph).
        
        Args:
            around_node: Center graph around this variable
            max_nodes: Maximum nodes to return
            radius: Radius of ego graph
            
        Returns:
            Tuple of (nodes, edges) for Cytoscape
        """
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        seen_nodes: Set[str] = set()
        
        if self._state is None or self._state._pointer_flow_graph is None:
            return nodes, edges
        
        try:
            pfg = self._state._pointer_flow_graph
            
            # If no center specified, just take some edges
            if around_node is None:
                for edge in list(pfg.edges)[:max_nodes * 2]:
                    self._add_pfg_edge_to_graph(edge, nodes, edges, seen_nodes)
            else:
                # Find matching node and do BFS
                center_nodes = []
                for node in pfg.nodes:
                    node_str = str(node)
                    if around_node in node_str:
                        center_nodes.append(node)
                        if len(center_nodes) >= 5:
                            break
                
                # BFS from center nodes
                visited = set(center_nodes)
                frontier = list(center_nodes)
                
                for _ in range(radius):
                    next_frontier = []
                    for node in frontier:
                        # Add successors
                        for edge in pfg.succs.get(node, set()):
                            if edge.target not in visited and len(seen_nodes) < max_nodes:
                                visited.add(edge.target)
                                next_frontier.append(edge.target)
                            self._add_pfg_edge_to_graph(edge, nodes, edges, seen_nodes)
                        
                        # Add predecessors
                        for edge in pfg.preds.get(node, set()):
                            if edge.source not in visited and len(seen_nodes) < max_nodes:
                                visited.add(edge.source)
                                next_frontier.append(edge.source)
                            self._add_pfg_edge_to_graph(edge, nodes, edges, seen_nodes)
                    
                    frontier = next_frontier
                    if len(seen_nodes) >= max_nodes:
                        break
                        
        except Exception as e:
            logger.error(f"Error getting PFG: {e}")
        
        return nodes, edges
    
    def _add_pfg_edge_to_graph(
        self,
        edge,
        nodes: List[GraphNode],
        edges: List[GraphEdge],
        seen_nodes: Set[str]
    ) -> None:
        """Helper to add a PFG edge to the graph data."""
        source_str = str(edge.source)
        target_str = str(edge.target)
        
        source_id = source_str
        target_id = target_str
        
        if source_id not in seen_nodes:
            nodes.append(GraphNode(
                id=source_id,
                label=source_str,
                node_type='variable',
                data={'full': str(edge.source)}
            ))
            seen_nodes.add(source_id)
        
        if target_id not in seen_nodes:
            nodes.append(GraphNode(
                id=target_id,
                label=target_str,
                node_type='variable',
                data={'full': str(edge.target)}
            ))
            seen_nodes.add(target_id)
        
        edges.append(GraphEdge(
            source=source_id,
            target=target_id,
            edge_type=f"pfg_{edge.kind.value}" if hasattr(edge.kind, 'value') else 'pfg_normal',
            data={'kind': edge.kind.value if hasattr(edge.kind, 'value') else ''}
        ))
    
    def compute_delta(self, k: int) -> DeltaSummary:
        """Compute delta from iteration k to current.
        
        Args:
            k: Starting iteration (exclusive)
            
        Returns:
            DeltaSummary with changes since iteration k
        """
        return self.delta_tracker.compute_delta(k)
    
    def get_hot_spots(self) -> HotSpots:
        """Get hot spots for debugging - problematic areas.
        
        Returns:
            HotSpots with failure locations and coverage issues
        """
        top_failed = []
        empty_pts = []
        uncalled = []
        low_coverage = []
        
        if self._state is None:
            return HotSpots(
                top_failed_callsites=top_failed,
                empty_pts_variables=empty_pts,
                uncalled_functions=uncalled,
                low_coverage_modules=low_coverage
            )
        
        try:
            # Find empty points-to variables
            for var, pts in self._state._env.items():
                if len(pts) == 0:
                    var_str = str(var)
                    # Focus on user-visible variables
                    if not var_str.startswith('$') and 'tmp' not in var_str.lower():
                        empty_pts.append(var_str[:100])
                        if len(empty_pts) >= 100:
                            break
            
            # Aggregate call failures from tracker
            failure_counts: Dict[str, Dict[str, Any]] = defaultdict(lambda: {'count': 0, 'reason': ''})
            from .models import DeltaEventType
            for event in self.delta_tracker._events.get(DeltaEventType.CALL_FAILED, []):
                site = event.data.get('call_site', 'unknown')
                failure_counts[site]['count'] += 1
                failure_counts[site]['reason'] = event.data.get('reason', '')
            
            # Sort by failure count
            top_failed = [
                {'call_site': site, 'failure_count': data['count'], 'reason': data['reason']}
                for site, data in sorted(failure_counts.items(), key=lambda x: -x[1]['count'])[:30]
            ]
            
            # Find functions with allocations but no call edges
            if self._state._call_graph:
                called_functions = set()
                for edge in self._state._call_graph.edges:
                    callee_name = str(edge.callee.stmt.get_qualname() if hasattr(edge.callee.stmt, 'get_qualname') else edge.callee)
                    called_functions.add(callee_name)
                
                # Check objects for function allocations
                for var, pts in self._state._env.items():
                    for obj in pts:
                        if hasattr(obj, 'kind') and obj.kind.value in ('function', 'method'):
                            func_name = str(obj.alloc_site.stmt.get_qualname() if hasattr(obj.alloc_site.stmt, 'get_qualname') else obj)[:100]
                            if func_name not in called_functions:
                                uncalled.append({
                                    'name': func_name,
                                    'object_count': 1
                                })
                            if len(uncalled) >= 50:
                                break
                    if len(uncalled) >= 50:
                        break
                        
        except Exception as e:
            logger.error(f"Error computing hot spots: {e}")
        
        return HotSpots(
            top_failed_callsites=top_failed,
            empty_pts_variables=empty_pts,
            uncalled_functions=uncalled,
            low_coverage_modules=low_coverage
        )
    
    def get_constraints_for_variable(self, variable_str: str) -> List[str]:
        """Get constraints involving a variable.
        
        Args:
            variable_str: Variable string to search for
            
        Returns:
            List of constraint strings
        """
        constraints = []
        
        if self._state is None:
            return constraints
        
        try:
            for var, var_constraints in self._state.constraints._by_variable.items():
                if variable_str in str(var):
                    for scope, constraint in var_constraints:
                        constraints.append(str(constraint)[:200])
                        if len(constraints) >= 50:
                            break
                if len(constraints) >= 50:
                    break
        except Exception as e:
            logger.error(f"Error getting constraints: {e}")
        
        return constraints
    
    def get_unknown_summary(self) -> Dict[str, Any]:
        """Get summary of unknown/unmodeled elements."""
        if self._solver is None:
            return {}
        
        try:
            return self._solver._unknown_tracker.get_summary()
        except Exception:
            return {}
    
    def get_unknown_details(self) -> List[Dict[str, Any]]:
        """Get detailed unknown/unmodeled elements."""
        if self._solver is None:
            return []
        
        try:
            return self._solver._unknown_tracker.get_detailed_report()
        except Exception:
            return []


# Global backend instance
_backend: Optional[PTAMonitorBackend] = None


def get_backend() -> PTAMonitorBackend:
    """Get the global backend instance."""
    global _backend
    if _backend is None:
        _backend = PTAMonitorBackend()
    return _backend

