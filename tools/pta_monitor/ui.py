"""NiceGUI frontend for PTA Monitor.

Text-first interactive debugger UI for k-CFA pointer analysis.
Focus on making missing call edges debuggable.
"""

import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

from nicegui import ui, app
from nicegui.events import ValueChangeEventArguments


from .backend import get_backend, PTAMonitorBackend
from .models import ModuleInfo, AnalysisStatus, DeltaSummary

# All 16 context sensitivity policies
ALL_POLICIES = [
    # Call-string sensitivity (k-CFA)
    "0-cfa",    # Context-insensitive baseline
    "1-cfa",    # 1-CFA
    "2-cfa",    # 2-CFA (default)
    "3-cfa",    # 3-CFA
    
    # Object sensitivity
    "1-obj",    # 1-object sensitive
    "2-obj",    # 2-object sensitive
    "3-obj",    # 3-object sensitive
    
    # Param sensitivity
    "1-param",   # 1-param sensitive
    "2-param",   # 2-param sensitive
    "3-param",   # 3-param sensitive
    
    # Type sensitivity
    "1-type",   # 1-type sensitive
    "2-type",   # 2-type sensitive
    "3-type",   # 3-type sensitive
    
    # Receiver sensitivity
    "1-rcv",    # 1-receiver sensitive
    "2-rcv",    # 2-receiver sensitive
    "3-rcv",    # 3-receiver sensitive
    
    # Hybrid policies
    "1c1o",     # 1-call + 1-object
    "2c1o",     # 2-call + 1-object
    "1c2o",     # 1-call + 2-object
]

# ==================== UI State ====================

class UIState:
    """Global UI state."""
    
    def __init__(self):
        self.selected_module: Optional[str] = None
        self.selected_ir_index: int = -1
        self.selected_ir_line: int = -1  # Current IR line number in Monaco editor
        self.k_lookback: int = 0
        self.search_filter: str = ""
        self.show_only_failures: bool = False
        
        # Current module data
        self.current_module_info: Optional[ModuleInfo] = None
        
        # Click handlers (set by setup_js_event_bridges)
        self._on_source_click = None
        self._on_ir_click = None
        
        # UI element references (not stored in app.storage to avoid JSON serialization issues)
        self.source_editor = None
        self.ir_table = None
        self.inspector_text = None
        self.delta_text = None
        self.status_label = None
        self.iteration_label = None
        self.call_graph_container = None
        self.pfg_graph_container = None
        self.graph_info_text = None
        self.module_select = None
        self.hotspots_text = None
        self.unknowns_text = None


_ui_state = UIState()


# ==================== JavaScript Event Bridges ====================

def setup_js_event_bridges(backend: PTAMonitorBackend):
    """Setup JavaScript-to-Python event communication for Monaco editors."""
    
    async def on_source_line_clicked(line_number: int, column: int, line_content: str):
        """Handle source code line click - update inspector with source context."""
        if not _ui_state.inspector_text:
            return
            
        lines = [
            "╔══════════════════════════════════════════════════════════════════╗",
            f"║  SOURCE LINE {line_number}",
            "╚══════════════════════════════════════════════════════════════════╝",
            "",
            f"Content: {line_content[:200]}",
            ""
        ]
        
        # Find corresponding IR lines
        if _ui_state.current_module_info:
            ir_line_nums = _ui_state.current_module_info.source_to_ir.get(line_number, [])
            if ir_line_nums:
                lines.append(f"═══ Corresponding IR Lines ({len(ir_line_nums)}) ═══")
                for ir_line_num in ir_line_nums[:10]:
                    # Find the IR statement
                    for stmt in _ui_state.current_module_info.ir_statements:
                        if stmt.line_number == line_number:
                            call_marker = " [CALL]" if stmt.is_call else ""
                            lines.append(f"  Line {ir_line_num}: {stmt.text[:80]}{call_marker}")
                            break
            else:
                lines.append("No corresponding IR statements found.")
        
        _ui_state.inspector_text.value = '\n'.join(lines)
    
    async def on_ir_line_clicked(line_number: int, column: int, line_content: str):
        """Handle IR line click - show detailed info in inspector/status bar."""
        _ui_state.selected_ir_line = line_number
        
        # Parse the line content
        line_content = line_content.strip()
        
        lines = [
            "╔══════════════════════════════════════════════════════════════════╗",
            f"║  IR LINE {line_number}",
            "╚══════════════════════════════════════════════════════════════════╝",
            "",
        ]
        
        # Check if it's a basic block label
        if line_content.startswith('bb') and line_content.endswith(':'):
            block_id = line_content[:-1]
            lines.append(f"Basic Block: {block_id}")
            lines.append("")
            lines.append("This is a basic block label. Basic blocks are sequences of")
            lines.append("statements with single entry and single exit points.")
            
        elif line_content.startswith('# Module:'):
            lines.append("Module header comment")
            
        elif line_content.startswith('def ') or line_content.startswith('class '):
            lines.append(f"Scope Definition: {line_content}")
            lines.append("")
            if line_content.startswith('def '):
                lines.append("This defines a function scope in the IR.")
            else:
                lines.append("This defines a class scope in the IR.")
                
        elif not line_content:
            lines.append("Empty line (block separator)")
            
        else:
            # It's an IR statement
            lines.append(f"Statement: {line_content[:150]}")
            lines.append("")
            
            # Find corresponding source line
            if _ui_state.current_module_info:
                source_line = _ui_state.current_module_info.ir_to_source.get(line_number)
                if source_line:
                    lines.append(f"Source line: {source_line}")
                    lines.append("")
            
            # Determine statement type
            stmt_type = "Unknown"
            if line_content.startswith('$'):
                if '= Call' in line_content or 'Call(' in line_content:
                    stmt_type = "Call (function/method invocation)"
                elif '= Attr' in line_content or '.Attr' in line_content:
                    stmt_type = "Attribute access"
                elif '= Subscr' in line_content or '[' in line_content:
                    stmt_type = "Subscript access"
                elif '=' in line_content:
                    stmt_type = "Assignment"
            elif 'return' in line_content.lower():
                stmt_type = "Return statement"
            elif 'import' in line_content.lower():
                stmt_type = "Import statement"
            
            lines.append(f"Statement Type: {stmt_type}")
            lines.append("")
            
            # Check if it's a call and get detailed info
            is_call = 'Call' in line_content or ('(' in line_content and '=' in line_content and 'Subscr' not in line_content)
            if is_call:
                lines.append("═══ Call Site Information ═══")
                
                # Try to get callsite info from backend
                callsite_info = backend.get_callsite_info(line_content[:100])
                if callsite_info:
                    lines.append(f"  Callee variable: {callsite_info.callee_var}")
                    lines.append(f"  Callee points-to size: {callsite_info.callee_pts_size}")
                    lines.append("")
                    
                    if callsite_info.resolved_edges:
                        lines.append(f"  ✓ Resolved Edges ({len(callsite_info.resolved_edges)}):")
                        for edge in callsite_info.resolved_edges[:5]:
                            caller = edge.get('caller', '?')[:40]
                            callee = edge.get('callee', '?')[:40]
                            lines.append(f"    → {callee}")
                        if len(callsite_info.resolved_edges) > 5:
                            lines.append(f"    ... and {len(callsite_info.resolved_edges) - 5} more")
                    else:
                        lines.append("  ⚠ No resolved call edges!")
                    
                    lines.append("")
                    if callsite_info.failures:
                        lines.append(f"  ✗ Failures ({len(callsite_info.failures)}):")
                        for fail in callsite_info.failures[:5]:
                            reason = fail.get('reason', 'unknown')
                            lines.append(f"    • {reason}")
                else:
                    lines.append("  (No call site information available)")
                lines.append("")
            
            # Extract variable names and show points-to info
            import re
            # Match IR variables like $tmp0, $const1, regular identifiers
            var_pattern = r'(\$[a-zA-Z_]\w*|\b[a-zA-Z_][a-zA-Z0-9_]*\b)'
            potential_vars = re.findall(var_pattern, line_content)
            
            # Filter out keywords and common non-variable tokens
            keywords = {'def', 'class', 'if', 'else', 'return', 'for', 'while', 'import', 
                       'from', 'as', 'Call', 'Attr', 'Subscr', 'True', 'False', 'None',
                       'and', 'or', 'not', 'in', 'is', 'bb'}
            potential_vars = [v for v in potential_vars if v not in keywords and not v.isdigit()]
            
            # Deduplicate while preserving order
            seen = set()
            unique_vars = []
            for v in potential_vars:
                if v not in seen:
                    seen.add(v)
                    unique_vars.append(v)
            
            if unique_vars:
                lines.append("═══ Variables ═══")
                for var in unique_vars[:5]:
                    pts_info = backend.get_points_to(var)
                    if pts_info.size > 0:
                        lines.append(f"  {var} → {pts_info.size} object(s)")
                        for obj in pts_info.objects[:3]:
                            obj_str = obj.get('str', str(obj))[:60]
                            lines.append(f"    • {obj_str}")
                        if pts_info.size > 3:
                            lines.append(f"    ... and {pts_info.size - 3} more")
                    else:
                        lines.append(f"  {var} → (no points-to info)")
        
        if _ui_state.inspector_text:
            _ui_state.inspector_text.value = '\n'.join(lines)
    
    # Store the callbacks for use by the main page
    _ui_state._on_source_click = on_source_line_clicked
    _ui_state._on_ir_click = on_ir_line_clicked


# ==================== Main Page ====================

def create_ui():
    """Create the main PTA Monitor UI."""
    backend = get_backend()
    
    # Custom CSS for styling
    ui.add_head_html('''
    <style>
        :root {
            --primary-dark: #1a1a2e;
            --secondary-dark: #16213e;
            --accent: #e94560;
            --accent-secondary: #0f3460;
            --text-primary: #eee;
            --text-secondary: #aaa;
            --border-color: #333;
        }
        
        .dark-panel {
            background-color: var(--secondary-dark);
            border: 1px solid var(--border-color);
            border-radius: 4px;
        }
        
        .code-font {
            font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 12px;
        }
        
        .log-text {
            font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 11px;
            white-space: pre-wrap;
            word-break: break-all;
            background-color: #0d1117;
            color: #c9d1d9;
            padding: 8px;
            border-radius: 4px;
            overflow-y: auto;
            max-height: 300px;
        }
        
        .ir-row {
            cursor: pointer;
            padding: 2px 8px;
            border-bottom: 1px solid #333;
        }
        
        .ir-row:hover {
            background-color: #2d333b;
        }
        
        .ir-row.selected {
            background-color: var(--accent-secondary);
        }
        
        .ir-call {
            color: #58a6ff;
        }
        
        .status-running {
            color: #3fb950;
        }
        
        .status-complete {
            color: #8b949e;
        }
        
        .cytoscape-container {
            width: 100%;
            height: 200px;
            background-color: #0d1117;
            border: 1px solid var(--border-color);
            border-radius: 4px;
        }
    </style>
    ''')
    
    # Cytoscape.js for graph visualization
    ui.add_head_html('''
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.26.0/cytoscape.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
    ''')
    
    # Monaco Editor - CSS loaded immediately, JS loaded lazily to avoid conflicts with NiceGUI
    ui.add_head_html('''
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/editor/editor.main.min.css">
    <style>
        .monaco-container {
            width: 100%;
            height: 100%;
            min-height: 200px;
            border: 1px solid #444;
            border-radius: 6px;
            overflow: hidden;
            background-color: #1e1e1e;
        }
        .highlighted-line {
            background-color: rgba(255, 215, 0, 0.25) !important;
            border-left: 3px solid #ffd700 !important;
        }
        .highlighted-glyph {
            background-color: #ffd700;
            border-radius: 2px;
        }
        .call-site-line {
            background-color: rgba(88, 166, 255, 0.15) !important;
        }
        .monaco-loading {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #888;
            font-family: monospace;
        }
    </style>
    <script>
        // Global state - no conflicts with require
        window.ptaMonaco = {
            sourceEditor: null,
            irEditor: null,
            sourceToIrMap: {},
            irToSourceMap: {},
            lastSourceClick: null,
            lastIRClick: null,
            sourceDecorations: [],
            irDecorations: [],
            loaded: false,
            loading: false,
            callbacks: []
        };
        
        // Load Monaco lazily - called after page is ready
        window.loadMonacoLazy = function(callback) {
            if (window.ptaMonaco.loaded) {
                if (callback) callback();
                return;
            }
            
            if (callback) {
                window.ptaMonaco.callbacks.push(callback);
            }
            
            if (window.ptaMonaco.loading) {
                return;
            }
            
            window.ptaMonaco.loading = true;
            console.log('Loading Monaco editor...');
            
            // Load the loader script dynamically
            var script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs/loader.min.js';
            script.onload = function() {
                // Configure require with Monaco paths
                require.config({ 
                    paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' }
                });
                
                // Load Monaco editor main
                require(['vs/editor/editor.main'], function() {
                    window.ptaMonaco.loaded = true;
                    window.ptaMonaco.loading = false;
                    console.log('Monaco editor loaded successfully');
                    
                    // Execute all pending callbacks
                    var cbs = window.ptaMonaco.callbacks;
                    window.ptaMonaco.callbacks = [];
                    cbs.forEach(function(cb) { cb(); });
                });
            };
            script.onerror = function() {
                console.error('Failed to load Monaco editor');
                window.ptaMonaco.loading = false;
            };
            document.head.appendChild(script);
        };
        
        // Initialize Monaco editor
        window.initMonacoEditor = function(containerId, language, readOnly, onClickCallback) {
            window.loadMonacoLazy(function() {
                var container = document.getElementById(containerId);
                if (!container) {
                    console.error('Container not found:', containerId);
                    return;
                }
                
                // Destroy existing editor if any
                if (containerId === 'source-editor-container' && window.ptaMonaco.sourceEditor) {
                    window.ptaMonaco.sourceEditor.dispose();
                    window.ptaMonaco.sourceEditor = null;
                }
                if (containerId === 'ir-editor-container' && window.ptaMonaco.irEditor) {
                    window.ptaMonaco.irEditor.dispose();
                    window.ptaMonaco.irEditor = null;
                }
                
                // Clear container
                container.innerHTML = '';
                
                // Create editor with Python highlighting
                var editor = monaco.editor.create(container, {
                    value: '',
                    language: 'python',
                    theme: 'vs-dark',
                    readOnly: readOnly,
                    automaticLayout: true,
                    minimap: { enabled: true, showSlider: 'mouseover' },
                    lineNumbers: 'on',
                    scrollBeyondLastLine: false,
                    fontSize: 13,
                    fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', 'Consolas', monospace",
                    renderLineHighlight: 'all',
                    cursorStyle: 'line',
                    wordWrap: 'off',
                    glyphMargin: true,
                    folding: true,
                    smoothScrolling: true,
                    mouseWheelZoom: true,
                    tabSize: 4,
                    renderWhitespace: 'selection',
                    bracketPairColorization: { enabled: true },
                    guides: { indentation: true, bracketPairs: true }
                });
                
                // Store reference
                if (containerId === 'source-editor-container') {
                    window.ptaMonaco.sourceEditor = editor;
                } else if (containerId === 'ir-editor-container') {
                    window.ptaMonaco.irEditor = editor;
                }
                
                // Add click handler for line clicks
                editor.onMouseDown(function(e) {
                    var position = e.target.position;
                    if (position) {
                        var lineNumber = position.lineNumber;
                        var lineContent = editor.getModel().getLineContent(lineNumber);
                        var column = position.column;
                        
                        if (containerId === 'source-editor-container') {
                            window.ptaMonaco.lastSourceClick = { 
                                line: lineNumber, 
                                column: column,
                                content: lineContent.substring(0, 500)
                            };
                            
                            // Navigate to corresponding IR line
                            var irLines = window.ptaMonaco.sourceToIrMap[String(lineNumber)];
                            if (irLines && irLines.length > 0 && window.ptaMonaco.irEditor) {
                                window.goToLine('ir-editor-container', irLines[0]);
                            }
                        } else if (containerId === 'ir-editor-container') {
                            window.ptaMonaco.lastIRClick = { 
                                line: lineNumber, 
                                column: column,
                                content: lineContent.substring(0, 500)
                            };
                            
                            // Navigate to corresponding source line
                            var sourceLine = window.ptaMonaco.irToSourceMap[String(lineNumber)];
                            if (sourceLine && window.ptaMonaco.sourceEditor) {
                                window.goToLine('source-editor-container', sourceLine);
                            }
                        }
                    }
                });
                
                console.log('Monaco editor initialized for', containerId);
            });
        };
        
        // Update editor content
        window.setEditorContent = function(containerId, content) {
            if (containerId === 'source-editor-container' && window.ptaMonaco.sourceEditor) {
                window.ptaMonaco.sourceEditor.setValue(content);
            } else if (containerId === 'ir-editor-container' && window.ptaMonaco.irEditor) {
                window.ptaMonaco.irEditor.setValue(content);
            }
        };
        
        // Go to line in editor with highlighting
        window.goToLine = function(containerId, lineNumber) {
            var editor = null;
            var decorationsRef = null;
            
            if (containerId === 'source-editor-container') {
                editor = window.ptaMonaco.sourceEditor;
                decorationsRef = 'sourceDecorations';
            } else if (containerId === 'ir-editor-container') {
                editor = window.ptaMonaco.irEditor;
                decorationsRef = 'irDecorations';
            }
            
            if (editor && lineNumber > 0) {
                editor.revealLineInCenter(lineNumber);
                editor.setPosition({ lineNumber: lineNumber, column: 1 });
                editor.focus();
                
                // Clear previous decorations
                if (window.ptaMonaco[decorationsRef] && window.ptaMonaco[decorationsRef].length > 0) {
                    editor.deltaDecorations(window.ptaMonaco[decorationsRef], []);
                }
                
                // Add highlight decoration
                window.ptaMonaco[decorationsRef] = editor.deltaDecorations([], [{
                    range: new monaco.Range(lineNumber, 1, lineNumber, 1),
                    options: {
                        isWholeLine: true,
                        className: 'highlighted-line',
                        glyphMarginClassName: 'highlighted-glyph',
                        overviewRuler: {
                            color: '#ffd700',
                            position: monaco.editor.OverviewRulerLane.Full
                        }
                    }
                }]);
                
                // Remove decoration after 3 seconds
                setTimeout(function() {
                    if (editor && window.ptaMonaco[decorationsRef]) {
                        editor.deltaDecorations(window.ptaMonaco[decorationsRef], []);
                        window.ptaMonaco[decorationsRef] = [];
                    }
                }, 3000);
            }
        };
        
        // Update source-to-IR and IR-to-source mappings
        window.updateMappings = function(sourceToIr, irToSource) {
            window.ptaMonaco.sourceToIrMap = sourceToIr || {};
            window.ptaMonaco.irToSourceMap = irToSource || {};
            console.log('Updated mappings:', Object.keys(window.ptaMonaco.sourceToIrMap).length, 'source lines,', 
                        Object.keys(window.ptaMonaco.irToSourceMap).length, 'IR lines');
        };
    </script>
    ''')
    
    ui.dark_mode().enable()
    
    # Setup JavaScript-to-Python event bridges
    setup_js_event_bridges(backend)
    
    # Main layout
    with ui.column().classes('w-full h-screen p-0 m-0'):
        # Top toolbar
        create_toolbar(backend)
        
        # Main content area
        with ui.row().classes('w-full h-full flex-1 gap-0').style('min-height: 0'):
            # Left: Source + IR panels (60% width)
            with ui.column().classes('w-full h-full flex-1').style('width: 70%; min-width: 200px'):
                create_main_panels(backend)
                # Bottom: Status bar with tabs
                create_bottom_tabs(backend)
            
            # Right: Graph sidebar (40% width)
            with ui.column().classes('w-full h-full').style('width: 30%; min-width: 0'):
                create_graph_sidebar(backend)


def create_toolbar(backend: PTAMonitorBackend):
    """Create the top toolbar."""
    with ui.row().classes('w-full items-center gap-4 p-2 bg-gray-900 border-b border-gray-700'):
        # Logo/Title
        ui.label('🔬 PTA Monitor').classes('text-xl font-bold text-white')
        
        # Module dropdown
        module_select = ui.select(
            options=[],
            label='Module',
            on_change=lambda e: on_module_selected(e.value, backend)
        ).classes('w-64')
        
        # Store reference for updates (in _ui_state, not app.storage to avoid JSON issues)
        _ui_state.module_select = module_select
        
        ui.separator().props('vertical')
        
        # Iteration display
        _ui_state.iteration_label = ui.label('Iter: 0').classes('text-green-400 font-mono')
        
        # Status display
        _ui_state.status_label = ui.label('READY').classes('text-gray-400 font-mono')
        
        ui.separator().props('vertical')
        
        # Analysis controls
        def start_analysis():
            dialog = ui.dialog()
            with dialog, ui.card().classes('p-4'):
                ui.label('Start Analysis').classes('text-lg font-bold')
                path_input = ui.input('Target Path', value=backend.get_default_target_path()).classes('w-96')
                policy_select = ui.select(
                    options=ALL_POLICIES,
                    value=backend.get_default_context_policy(),
                    label='Context Policy'
                )
                max_iter = ui.number('Max Iterations', value=backend.get_default_max_iterations(), min=1)
                
                with ui.row().classes('gap-2 mt-4'):
                    ui.button('Start', on_click=lambda: [
                        backend.start_analysis(
                            path_input.value,
                            policy_select.value,
                            int(max_iter.value)
                        ),
                        dialog.close(),
                        start_status_updates()
                    ]).classes('bg-green-600')
                    ui.button('Cancel', on_click=dialog.close).classes('bg-gray-600')
            
            dialog.open()
        
        ui.button('▶ Start', on_click=start_analysis).classes('bg-green-700 hover:bg-green-600')
        ui.button('⬛ Stop', on_click=backend.stop_analysis).classes('bg-red-700 hover:bg-red-600')
        
        ui.separator().props('vertical')
        
        # K-lookback input
        ui.label('k:').classes('text-gray-300')
        k_input = ui.number(value=0, min=0).classes('w-20')
        k_input.on('change', lambda e: setattr(_ui_state, 'k_lookback', int(e.value or 0)))
        
        ui.button('Compute Delta', on_click=lambda: update_delta_tab(backend)).classes('bg-blue-700')
        
        ui.separator().props('vertical')
        
        # Search filter
        search_input = ui.input(placeholder='Search...').classes('w-48')
        search_input.on('change', lambda e: setattr(_ui_state, 'search_filter', e.value or ''))
        
        ui.checkbox('Failures only', on_change=lambda e: setattr(_ui_state, 'show_only_failures', e.value))


def create_main_panels(backend: PTAMonitorBackend):
    """Create the source and IR panels with Monaco editors."""
    with ui.splitter(value=50).classes('w-full flex-1') as splitter:
        # Left side - Source Code
        with splitter.before:
            with ui.column().classes('w-full h-full p-2 gap-1'):
                ui.label('Source Code (Python)').classes('text-sm font-bold text-gray-400')
                
                # Monaco editor container for source
                _ui_state.source_editor = ui.element('div').props(
                    'id=source-editor-container'
                ).classes('w-full flex-grow monaco-container')

        # Right side - IR with basic blocks
        with splitter.after:
            with ui.column().classes('w-full h-full p-2 gap-1'):
                ui.label('IR Statements (Basic Blocks)').classes('text-sm font-bold text-gray-400')
                
                # Monaco editor container for IR
                _ui_state.ir_table = ui.element('div').props(
                    'id=ir-editor-container'
                ).classes('w-full flex-grow monaco-container')
    
    # Initialize Monaco editors after DOM is ready
    async def init_editors():
        await asyncio.sleep(0.5)  # Wait for DOM to be ready
        
        # Initialize source editor (Python highlighting, read-only)
        ui.run_javascript('''
            window.initMonacoEditor('source-editor-container', 'python', true, null);
        ''')
        
        # Initialize IR editor (Python highlighting for readability, read-only)
        ui.run_javascript('''
            window.initMonacoEditor('ir-editor-container', 'python', true, null);
        ''')
        
        # Set default placeholder content
        ui.run_javascript('''
            setTimeout(function() {
                window.setEditorContent('source-editor-container', '# Select a module from the dropdown above to view source code\\n\\n# The source code will be displayed here with Python syntax highlighting.\\n# Click on any line to see corresponding IR statements on the right.');
                window.setEditorContent('ir-editor-container', '# Select a module to view IR\\n\\n# The IR (Intermediate Representation) will be displayed here.\\n# IR is organized into basic blocks (bb0:, bb1:, etc.)\\n# Click on any IR element to see detailed information in the Inspector tab below.');
            }, 800);
        ''')
    
    # Poll for clicks and update inspector
    async def poll_clicks():
        """Poll for editor clicks and update the inspector."""
        try:
            # Check for source clicks (use ptaMonaco namespace)
            source_result = await ui.run_javascript('''
                (function() {
                    if (window.ptaMonaco && window.ptaMonaco.lastSourceClick) {
                        var click = window.ptaMonaco.lastSourceClick;
                        window.ptaMonaco.lastSourceClick = null;
                        return click;
                    }
                    return null;
                })()
            ''', timeout=0.3)
            
            if source_result and _ui_state._on_source_click:
                await _ui_state._on_source_click(
                    source_result.get('line', 0),
                    source_result.get('column', 0),
                    source_result.get('content', '')
                )
        except Exception:
            pass  # Ignore timeout/errors
        
        try:
            # Check for IR clicks (use ptaMonaco namespace)
            ir_result = await ui.run_javascript('''
                (function() {
                    if (window.ptaMonaco && window.ptaMonaco.lastIRClick) {
                        var click = window.ptaMonaco.lastIRClick;
                        window.ptaMonaco.lastIRClick = null;
                        return click;
                    }
                    return null;
                })()
            ''', timeout=0.3)
            
            if ir_result and _ui_state._on_ir_click:
                await _ui_state._on_ir_click(
                    ir_result.get('line', 0),
                    ir_result.get('column', 0),
                    ir_result.get('content', '')
                )
        except Exception:
            pass  # Ignore timeout/errors
    
    # Schedule editor initialization
    ui.timer(0.1, init_editors, once=True)
    
    # Start click polling timer (poll every 250ms for responsive feel)
    ui.timer(0.25, poll_clicks)


def create_graph_sidebar(backend: PTAMonitorBackend):
    # 最外层 Column：h-full + flex-col 确保高度传递
    with ui.column().classes('h-full w-full p-2 bg-gray-900 flex flex-col gap-0'):  # flex flex-col 显式声明 Flex 容器
        
        # --- Graph 1 ---
        ui.label('Call Graph').classes('text-sm font-bold text-gray-400')
        
        # 修改 classes: flex-1 (grow=1, shrink=1, basis=0) + overflow-hidden 防止膨胀
        _ui_state.call_graph_container = ui.element('div').props('id=cy-call-graph').classes(
            'w-full flex-1 border border-gray-800 relative bg-[#0d1117] overflow-hidden'
        )
        
        # 初始提示文字（渲染后会被 Cytoscape 覆盖）
        with _ui_state.call_graph_container:
            ui.label('Click Refresh to load graph').classes(
                'absolute inset-0 flex items-center justify-center text-gray-500'
            )
        
        with ui.row().classes('gap-2 mt-1 mb-2 shrink-0'):  # shrink-0 防止按钮被挤
            ui.button('Refresh', on_click=lambda: update_call_graph(backend)).props('size=sm').classes('bg-gray-700')
            ui.button('Module Only', on_click=lambda: update_call_graph(backend, module_filter=_ui_state.selected_module)).props('size=sm').classes('bg-gray-700')
        
        ui.separator().classes('my-1 shrink-0')
        
        # --- Graph 2 ---
        ui.label('Pointer Flow Graph').classes('text-sm font-bold text-gray-400')
        
        # 同样的 classes 修改
        _ui_state.pfg_graph_container = ui.element('div').props('id=cy-pfg-graph').classes(
            'w-full flex-1 border border-gray-800 relative bg-[#0d1117] overflow-hidden'
        )
        
        with _ui_state.pfg_graph_container:
            ui.label('Enter variable and click Show').classes(
                'absolute inset-0 flex items-center justify-center text-gray-500'
            )
        
        with ui.row().classes('gap-2 mt-1 mb-2 shrink-0'):
            pfg_search = ui.input(placeholder='Variable...').classes('w-32').props('size=sm dense')
            ui.button('Show', on_click=lambda: update_pfg_graph(backend, pfg_search.value)).props('size=sm').classes('bg-gray-700')
        
        ui.separator().classes('my-1 shrink-0')
        
        # --- Info (固定高度，shrink-0 防止被挤) ---
        ui.label('Selection Info').classes('text-sm font-bold text-gray-400')
        _ui_state.graph_info_text = ui.textarea(value='Click a node/edge for details').classes('w-full').style('height: 200px').props('readonly filled square').classes('shrink-0')


def create_bottom_tabs(backend: PTAMonitorBackend):
    """Create the bottom status bar with tabs."""
    with ui.card().classes('w-full').style('height: 300px'):
        with ui.tabs().classes('w-full') as tabs:
            tab_inspector = ui.tab('Inspector')
            tab_delta = ui.tab('Delta (iter-k → iter)')
            tab_hotspots = ui.tab('Hot Spots')
            tab_unknowns = ui.tab('Unknowns')
        
        with ui.tab_panels(tabs, value=tab_inspector).classes('w-full h-full'):
            # Inspector tab
            with ui.tab_panel(tab_inspector).classes('p-2'):
                _ui_state.inspector_text = ui.textarea(
                    value='Click an IR statement to inspect...'
                ).classes('w-full log-text').style('height: 180px')
            
            # Delta tab
            with ui.tab_panel(tab_delta).classes('p-2'):
                _ui_state.delta_text = ui.textarea(
                    value='Set k and click "Compute Delta" to see changes since iteration k'
                ).classes('w-full log-text').style('height: 180px')
            
            # Hot Spots tab
            with ui.tab_panel(tab_hotspots).classes('p-2'):
                with ui.column().classes('w-full'):
                    ui.button('Refresh Hot Spots', on_click=lambda: update_hotspots(backend)).classes('bg-orange-700')
                    _ui_state.hotspots_text = ui.textarea(
                        value='Click refresh to load hot spots...'
                    ).classes('w-full log-text').style('height: 150px')
            
            # Unknowns tab
            with ui.tab_panel(tab_unknowns).classes('p-2'):
                with ui.column().classes('w-full'):
                    ui.button('Refresh Unknowns', on_click=lambda: update_unknowns(backend)).classes('bg-yellow-700')
                    _ui_state.unknowns_text = ui.textarea(
                        value='Click refresh to load unknown/unmodeled elements...'
                    ).classes('w-full log-text').style('height: 150px')


# ==================== Event Handlers ====================

def on_module_selected(qualname: str, backend: PTAMonitorBackend):
    """Handle module selection."""
    if not qualname:
        return
    
    _ui_state.selected_module = qualname
    
    # Get module info
    module_info = backend.get_module_info(qualname)
    if module_info is None:
        return
    
    # Store module info for click handlers
    _ui_state.current_module_info = module_info
    
    # Escape content for JavaScript
    source_escaped = json.dumps(module_info.source_code)
    ir_escaped = json.dumps(module_info.ir_text if module_info.ir_text else "# No IR available")
    
    # Update Monaco editors via JavaScript
    ui.run_javascript(f'''
        setTimeout(function() {{
            window.setEditorContent('source-editor-container', {source_escaped});
            window.setEditorContent('ir-editor-container', {ir_escaped});
            window.updateMappings({json.dumps(module_info.source_to_ir)}, {json.dumps(module_info.ir_to_source)});
        }}, 100);
    ''')


def on_ir_statement_clicked(stmt, backend: PTAMonitorBackend):
    """Handle IR statement click."""
    _ui_state.selected_ir_index = stmt.index
    
    # Get detailed info
    info = backend.get_ir_statement_info(
        _ui_state.selected_module or '',
        stmt.index
    )
    
    # Build inspector text
    lines = [
        f"=== IR Statement #{stmt.index} ===",
        f"Statement: {info.get('statement', stmt.text)}",
        f"Module: {info.get('module', '')}",
        ""
    ]
    
    # Variables and points-to
    if info.get('variables'):
        lines.append("--- Variables ---")
        for var in info['variables']:
            pts_info = info.get('points_to', {}).get(var, {})
            lines.append(f"  {var}")
            lines.append(f"    size: {pts_info.get('size', 0)}")
            for obj in pts_info.get('objects', [])[:5]:
                lines.append(f"    - {obj}")
        lines.append("")
    
    # Call info
    if info.get('call_info'):
        lines.append("--- Call Information ---")
        lines.append(f"  Is call: {info['call_info'].get('is_call', False)}")
        
        # Try to get callsite details
        call_site_str = stmt.text.split('=')[0].strip() if '=' in stmt.text else stmt.text
        callsite_info = backend.get_callsite_info(call_site_str)
        if callsite_info:
            lines.append(f"  Callee variable: {callsite_info.callee_var}")
            lines.append(f"  Callee pts size: {callsite_info.callee_pts_size}")
            lines.append(f"  Resolved edges: {len(callsite_info.resolved_edges)}")
            for edge in callsite_info.resolved_edges[:5]:
                lines.append(f"    - {edge}")
            lines.append(f"  Failures: {len(callsite_info.failures)}")
            for fail in callsite_info.failures[:5]:
                lines.append(f"    - {fail}")
        lines.append("")
    
    # Constraints
    if info.get('constraints'):
        lines.append("--- Related Constraints ---")
        for c in info['constraints'][:10]:
            lines.append(f"  {c}")
    
    # Update inspector
    if _ui_state.inspector_text:
        _ui_state.inspector_text.value = '\n'.join(lines)


def update_delta_tab(backend: PTAMonitorBackend):
    """Update the delta tab with changes since iteration k."""
    k = _ui_state.k_lookback
    delta = backend.compute_delta(k)
    
    if _ui_state.delta_text:
        _ui_state.delta_text.value = delta.to_text()


def update_hotspots(backend: PTAMonitorBackend):
    """Update the hot spots tab."""
    hotspots = backend.get_hot_spots()
    
    if _ui_state.hotspots_text:
        _ui_state.hotspots_text.value = hotspots.to_text()


def update_unknowns(backend: PTAMonitorBackend):
    """Update the unknowns tab."""
    summary = backend.get_unknown_summary()
    details = backend.get_unknown_details()
    
    lines = ["=== Unknown/Unmodeled Elements ===", ""]
    
    if summary:
        lines.append("--- Summary ---")
        for k, v in summary.items():
            lines.append(f"  {k}: {v}")
        lines.append("")
    
    if details:
        lines.append("--- Details (first 50) ---")
        for item in details[:50]:
            lines.append(f"  {item}")
    
    if _ui_state.unknowns_text:
        _ui_state.unknowns_text.value = '\n'.join(lines)


def update_call_graph(backend: PTAMonitorBackend, module_filter: Optional[str] = None):
    """Update call graph visualization."""
    nodes, edges = backend.get_call_graph_data(module_filter=module_filter)
    
    if not _ui_state.call_graph_container:
        return
    
    # Build Cytoscape elements
    elements = []
    for node in nodes:
        elements.append({
            'data': {
                'id': node.id,
                'label': node.label,
                **node.data
            }
        })
    for edge in edges:
        elements.append({
            'data': {
                'source': edge.source,
                'target': edge.target,
                **edge.data
            }
        })
    
    # Set container HTML (no script tags)
    container_id = 'cy-call-graph'
    _ui_state.call_graph_container.content = f'<div id="{container_id}" style="width:100%;height:200px;background:#0d1117;"></div>'
    _ui_state.call_graph_container.update()
    
    # Initialize Cytoscape via run_javascript
    js_code = f'''
    (function() {{
        // Wait a bit for DOM to update
        setTimeout(function() {{
            var container = document.getElementById('{container_id}');
            if (!container) {{
                console.error('Call graph container not found');
                return;
            }}
            
            // Destroy existing graph if any
            if (window.callGraphCy) {{
                window.callGraphCy.destroy();
            }}
            
            window.callGraphCy = cytoscape({{
                container: container,
                elements: {json.dumps(elements)},
                style: [
                    {{
                        selector: 'node',
                        style: {{
                            'label': 'data(label)',
                            'background-color': '#58a6ff',
                            'color': '#fff',
                            'font-size': '10px',
                            'text-valign': 'center',
                            'text-halign': 'center',
                            'width': '40px',
                            'height': '40px'
                        }}
                    }},
                    {{
                        selector: 'edge',
                        style: {{
                            'width': 2,
                            'line-color': '#8b949e',
                            'target-arrow-color': '#8b949e',
                            'target-arrow-shape': 'triangle',
                            'curve-style': 'bezier'
                        }}
                    }}
                ],
                layout: {{
                    name: 'dagre',
                    rankDir: 'TB',
                    nodeSep: 30,
                    rankSep: 50
                }}
            }});
            
            window.callGraphCy.on('tap', 'node', function(evt) {{
                var node = evt.target;
                console.log('Clicked node:', node.data());
            }});
            
            window.callGraphCy.on('tap', 'edge', function(evt) {{
                var edge = evt.target;
                console.log('Clicked edge:', edge.data());
            }});
        }}, 100);
    }})();
    '''
    
    ui.run_javascript(js_code)


def update_pfg_graph(backend: PTAMonitorBackend, around_node: Optional[str] = None):
    """Update PFG visualization."""
    nodes, edges = backend.get_pfg_data(around_node=around_node)
    
    if not _ui_state.pfg_graph_container:
        return
    
    # Build Cytoscape elements
    elements = []
    for node in nodes:
        elements.append({
            'data': {
                'id': node.id,
                'label': node.label[:20],
                **node.data
            }
        })
    for edge in edges:
        elements.append({
            'data': {
                'source': edge.source,
                'target': edge.target,
                **edge.data
            }
        })
    
    # Set container HTML (no script tags)
    container_id = 'cy-pfg-graph'
    _ui_state.pfg_graph_container.content = f'<div id="{container_id}" style="width:100%;height:200px;background:#0d1117;"></div>'
    _ui_state.pfg_graph_container.update()
    
    # Initialize Cytoscape via run_javascript
    js_code = f'''
    (function() {{
        // Wait a bit for DOM to update
        setTimeout(function() {{
            var container = document.getElementById('{container_id}');
            if (!container) {{
                console.error('PFG graph container not found');
                return;
            }}
            
            // Destroy existing graph if any
            if (window.pfgGraphCy) {{
                window.pfgGraphCy.destroy();
            }}
            
            window.pfgGraphCy = cytoscape({{
                container: container,
                elements: {json.dumps(elements)},
                style: [
                    {{
                        selector: 'node',
                        style: {{
                            'label': 'data(label)',
                            'background-color': '#f0883e',
                            'color': '#fff',
                            'font-size': '8px',
                            'text-valign': 'center',
                            'text-halign': 'center',
                            'width': '30px',
                            'height': '30px'
                        }}
                    }},
                    {{
                        selector: 'edge',
                        style: {{
                            'width': 1,
                            'line-color': '#6e7681',
                            'target-arrow-color': '#6e7681',
                            'target-arrow-shape': 'triangle',
                            'curve-style': 'bezier'
                        }}
                    }}
                ],
                layout: {{
                    name: 'dagre',
                    rankDir: 'LR',
                    nodeSep: 20,
                    rankSep: 40
                }}
            }});
            
            window.pfgGraphCy.on('tap', 'node', function(evt) {{
                var node = evt.target;
                console.log('Clicked PFG node:', node.data());
            }});
            
            window.pfgGraphCy.on('tap', 'edge', function(evt) {{
                var edge = evt.target;
                console.log('Clicked PFG edge:', edge.data());
            }});
        }}, 100);
    }})();
    '''
    
    ui.run_javascript(js_code)


async def update_status_loop(backend: PTAMonitorBackend):
    """Background loop to update status display."""
    while True:
        try:
            status = backend.get_status()
            
            if _ui_state.iteration_label:
                _ui_state.iteration_label.text = f'Iter: {status.current_iteration}'
            
            if _ui_state.status_label:
                if status.is_running:
                    _ui_state.status_label.text = f'RUNNING | WL:{status.worklist_size} | Edges:{status.num_plain_call_edges}'
                    _ui_state.status_label.classes('text-green-400')
                else:
                    _ui_state.status_label.text = f'COMPLETE | Edges:{status.num_plain_call_edges} | {status.elapsed_time:.1f}s'
                    _ui_state.status_label.classes('text-gray-400')
            
            # Update module dropdown if modules changed
            modules = backend.get_modules()
            if modules and _ui_state.module_select:
                options = {m['qualname']: m['display_name'] for m in modules}
                if options != _ui_state.module_select.options:
                    _ui_state.module_select.options = options
                    _ui_state.module_select.update()
            
        except Exception as e:
            pass
        
        await asyncio.sleep(0.5)


def start_status_updates():
    """Start the status update loop."""
    backend = get_backend()
    ui.timer(0.5, lambda: asyncio.create_task(update_status_once(backend)))


async def update_status_once(backend: PTAMonitorBackend):
    """Single status update."""
    try:
        status = backend.get_status()
        
        if _ui_state.iteration_label:
            _ui_state.iteration_label.text = f'Iter: {status.current_iteration}'
        
        if _ui_state.status_label:
            if status.is_running:
                _ui_state.status_label.text = f'RUNNING | WL:{status.worklist_size} | Edges:{status.num_plain_call_edges}'
            else:
                _ui_state.status_label.text = f'COMPLETE | Edges:{status.num_plain_call_edges} | {status.elapsed_time:.1f}s'
        
        # Update module dropdown
        modules = backend.get_modules()
        if modules and _ui_state.module_select:
            options = {m['qualname']: m['display_name'] for m in modules}
            _ui_state.module_select.options = options
            _ui_state.module_select.update()
            
    except Exception:
        pass


# ==================== Entry Point ====================

def run_monitor(port: int = 8080, host: str = '0.0.0.0', reload: bool = False):
    """Run the PTA Monitor web application.
    
    Args:
        port: Port to run on
        host: Host to bind to
        reload: Enable auto-reload for development
    """
    
    @ui.page('/')
    def main_page():
        create_ui()
    
    ui.run(
        port=port,
        host=host,
        reload=reload,
        title='PTA Monitor - Pointer Analysis Debugger',
        favicon='🔬',
        dark=True,
        storage_secret='pta_monitor_secret_key'
    )


if __name__ == '__main__':
    
    run_monitor()

