#!/usr/bin/env python3
"""
PTA Monitor - Interactive debugger UI for k-CFA pointer analysis.

This script starts the NiceGUI web application for debugging and monitoring
pointer analysis execution. It helps diagnose missing call edges and understand
points-to flow in Python programs.

Usage:
    python scripts/run_pta_monitor.py [OPTIONS]
    
    # Start monitor and analyze a file:
    python scripts/run_pta_monitor.py --target path/to/file.py
    
    # Start monitor with specific settings:
    python scripts/run_pta_monitor.py --target project/ --policy 2-call-site --port 8080
    
    # Just start the monitor (analyze via UI):
    python scripts/run_pta_monitor.py
"""

import argparse
import sys
import os
import logging
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def check_dependencies():
    """Check if required dependencies are installed."""
    missing = []
    
    try:
        import nicegui
    except ImportError:
        missing.append('nicegui')
    
    if missing:
        print("Missing required dependencies:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nInstall with:")
        print(f"  pip install {' '.join(missing)}")
        return False
    
    return True


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
    
    # Type sensitivity
    "1-type",   # 1-type sensitive
    "2-type",   # 2-type sensitive
    "3-type",   # 3-type sensitive
    
    # Param sensitivity
    "1-param",   # 1-param sensitive
    "2-param",   # 2-param sensitive
    "3-param",   # 3-param sensitive
    
    # Receiver sensitivity
    "1-rcv",    # 1-receiver sensitive
    "2-rcv",    # 2-receiver sensitive
    "3-rcv",    # 3-receiver sensitive
    
    # Hybrid policies
    "1c1o",     # 1-call + 1-object
    "2c1o",     # 2-call + 1-object
    "1c2o",     # 1-call + 2-object
]


def main():
    parser = argparse.ArgumentParser(
        description='PTA Monitor - Interactive debugger UI for k-CFA pointer analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Start monitor and analyze a file:
    python scripts/run_pta_monitor.py --target examples/simple.py
    
    # Start monitor for a project directory:
    python scripts/run_pta_monitor.py --target path/to/project/
    
    # Specify context sensitivity policy:
    python scripts/run_pta_monitor.py --target app.py --policy 2-call-site
    
    # Just start the monitor UI:
    python scripts/run_pta_monitor.py
        """
    )
    
    parser.add_argument(
        '--target', '-t',
        type=str,
        default=None,
        help='Target Python file or directory to analyze (can also specify via UI)'
    )
    
    parser.add_argument(
        '--policy', '-p',
        type=str,
        default='2-param',
        choices=ALL_POLICIES,
        help='Context sensitivity policy (default: 2-call-site)'
    )
    
    parser.add_argument(
        '--max-iterations', '-m',
        type=int,
        default=100000,
        help='Maximum solver iterations (default: 100000)'
    )
    
    parser.add_argument(
        '--port',
        type=int,
        default=8080,
        help='Port to run the web server on (default: 8080)'
    )
    
    parser.add_argument(
        '--host',
        type=str,
        default='127.0.0.1',
        help='Host to bind the web server to (default: 127.0.0.1)'
    )
    
    parser.add_argument(
        '--reload',
        action='store_true',
        help='Enable auto-reload for development'
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    # Set debug logging if requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Import and run the monitor
    try:
        from tools.pta_monitor.ui import run_monitor
        from tools.pta_monitor.backend import get_backend
        
        logger.info("Starting PTA Monitor...")
        logger.info(f"Server will be available at http://{args.host}:{args.port}")
        
        # If target specified, queue it for analysis
        if args.target:
            target_path = Path(args.target).resolve()
            if not target_path.exists():
                logger.error(f"Target path does not exist: {target_path}")
                sys.exit(1)
            
            logger.info(f"Will analyze: {target_path}")
            logger.info(f"Context policy: {args.policy}")
            logger.info(f"Max iterations: {args.max_iterations}")
            
            # Start analysis automatically
            backend = get_backend()
            # Note: Analysis will start after the UI is ready
            # This is handled via the UI's start function
            
            backend.set_default_values(args.target, args.policy, args.max_iterations, True)
        
        # Run the monitor
        run_monitor(
            port=args.port,
            host=args.host,
            reload=args.reload
        )
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.error("Make sure you're running from the project root directory")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting monitor: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

